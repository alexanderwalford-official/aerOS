/*
    tls.c - HTTPS: connecting BearSSL's TLS 1.2 client to AerOS (see tls.h for what TLS does)

    BearSSL is the "engine": it is handed bytes that came from the network (and gives back the
    bytes to send), and does the handshake, the encrypting and the certificate checks. It doesn't
    know about networks, clocks or random numbers, so this file gives it:

        the bytes     two small functions (read_from_network, write_to_network) that move
                      them over a TCP connection, which BearSSL's "sslio" layer calls
        randomness    32 bytes from crypto_random, to seed its own random number generator
        the date      from the CMOS clock, for the certificates' "valid from / until"
        the CAs       tls_roots (sys/tls_roots.c)

    And chooses what it may use: TLS 1.2, ECDHE key exchange, AES-GCM or ChaCha20-Poly1305,
    signatures with RSA or ECDSA (P-256, P-384, P-521). BearSSL's code for everything else isn't
    called, so the linker leaves it out.

    A connection takes about 55 KB of memory (BearSSL's state, and a buffer for one record), taken
    from the heap while it is open.
*/

#include "tls.h"
#include "tcp.h"
#include "net.h"
#include "sys_clck.h"
#include "trace.h"
#include "../lib/crypto.h"
#include "../lib/aer_string.h"
#include "../lib/aer_stdlib.h"
#include "../lib/bearssl/inc/bearssl.h"

// the CAs AerOS trusts: made by tools/gen_tls_roots.py, in sys/tls_roots.c
extern const br_x509_trust_anchor tls_roots[];
extern const size_t tls_roots_count;

#define HANDSHAKE_WAIT_MS 15000   // how long to wait for each step of making the connection

struct tls_connection {
    br_ssl_client_context client;   // BearSSL's TLS state
    br_x509_minimal_context x509;   // and its certificate checker
    br_sslio_context io;            // and the layer that calls our read and write functions
    unsigned char buffer[BR_SSL_BUFSIZE_MONO]; // room for one record of the connection
    int tcp;                        // the TCP connection under it
    uint32_t wait_ms;               // how long read_from_network waits for bytes
    int network_error;              // what went wrong in tcp_receive / tcp_send (0 if nothing)
    bool server_closed;             // the server closed the TCP connection
};

// ---- the network: what BearSSL reads and writes through ----------------------------------------

// BearSSL wants bytes from the server: up to `length` of them, into data. Gives back how many
// (more than 0), or -1 if there are none to be had (the reason is kept in the connection)
static int read_from_network (void* context, unsigned char* data, size_t length) {
    tls_connection_t* connection = context;
    int got = tcp_receive(connection->tcp, data, length, connection->wait_ms);
    if (got > 0) {
        return got;
    }
    if (got == 0) {
        connection->server_closed = true;
    } else {
        connection->network_error = got;
    }
    return -1;
}

// BearSSL has bytes for the server: send all of them. Gives back how many, or -1 if it failed
static int write_to_network (void* context, const unsigned char* data, size_t length) {
    tls_connection_t* connection = context;
    int result = tcp_send(connection->tcp, data, length);
    if (result != NET_OK) {
        connection->network_error = result;
        return -1;
    }
    return (int)length;
}

// ---- the date -----------------------------------------------------------------------------------

// Tell the certificate checker today's date, from the clock. It wants the days since 1 January of
// the year 0, and the seconds since midnight. (The days come from the usual calendar sum, which
// counts the year from March, so the leap day is the last day of it and every month follows the
// same pattern)
static void set_certificate_date (br_x509_minimal_context* x509) {
    rtc_time_t now;
    rtc_read(&now);
    uint32_t year = now.year;
    uint32_t month = now.month;
    if (month <= 2) {
        year--;
    }
    uint32_t era = year / 400;                               // 400 years: 146097 days, always
    uint32_t year_of_era = year - era * 400;
    uint32_t month_from_march = month > 2 ? month - 3 : month + 9;
    uint32_t day_of_year = (153 * month_from_march + 2) / 5 + now.day - 1;
    uint32_t day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    // from 1 March of the year 0 back to its 1 January is 60 days
    uint32_t days = era * 146097 + day_of_era + 60;
    uint32_t seconds = (uint32_t)now.hour * 3600 + (uint32_t)now.minute * 60 + now.second;
    br_x509_minimal_set_time(x509, days, seconds);
}

// ---- setting up ---------------------------------------------------------------------------------

// Fill in a new connection's BearSSL state: what it may use (see the top of this file), and the
// CAs and date to check the server's certificate against
static void configure (tls_connection_t* connection) {
    // the ciphers, best first: ChaCha20 is quicker than AES on a processor with no AES opcodes
    static const uint16_t suites[] = {
        BR_TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256,
        BR_TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256,
        BR_TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
        BR_TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
        BR_TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
        BR_TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
    };
    br_ssl_client_context* client = &connection->client;
    br_ssl_engine_context* engine = &client->eng;
    br_x509_minimal_context* x509 = &connection->x509;

    br_ssl_client_zero(client);
    br_ssl_engine_set_versions(engine, BR_TLS12, BR_TLS12);
    br_ssl_engine_set_suites(engine, suites, sizeof(suites) / sizeof(suites[0]));

    // the hashes (checksums) the handshake and the certificates use
    br_ssl_engine_set_hash(engine, br_sha256_ID, &br_sha256_vtable);
    br_ssl_engine_set_hash(engine, br_sha384_ID, &br_sha384_vtable);
    br_ssl_engine_set_hash(engine, br_sha512_ID, &br_sha512_vtable);
    br_ssl_engine_set_prf_sha256(engine, &br_tls12_sha256_prf);
    br_ssl_engine_set_prf_sha384(engine, &br_tls12_sha384_prf);

    // the key exchange and the signatures that prove who the server is (RSA, and the elliptic
    // curves P-256, P-384 and P-521). "i31" means they work in 31 bit pieces, which suits a
    // 32 bit processor
    br_ssl_engine_set_ec(engine, &br_ec_prime_i31);
    br_ssl_engine_set_rsavrfy(engine, &br_rsa_i31_pkcs1_vrfy);
    br_ssl_engine_set_ecdsa(engine, &br_ecdsa_i31_vrfy_asn1);

    // the ciphers: AES-GCM, and ChaCha20-Poly1305 (all written so that how long they take
    // doesn't depend on the secret, which could give it away)
    br_ssl_engine_set_aes_ctr(engine, &br_aes_ct_ctr_vtable);
    br_ssl_engine_set_ghash(engine, &br_ghash_ctmul32);
    br_ssl_engine_set_gcm(engine, &br_sslrec_in_gcm_vtable, &br_sslrec_out_gcm_vtable);
    br_ssl_engine_set_chacha20(engine, &br_chacha20_ct_run);
    br_ssl_engine_set_poly1305(engine, &br_poly1305_ctmul32_run);
    br_ssl_engine_set_chapol(engine, &br_sslrec_in_chapol_vtable, &br_sslrec_out_chapol_vtable);

    // the certificate checker: it hashes names with SHA-256, checks signatures with the same
    // RSA, ECDSA and hashes, trusts the CAs of tls_roots, and knows today's date
    br_x509_minimal_init(x509, &br_sha256_vtable, tls_roots, tls_roots_count);
    br_x509_minimal_set_hash(x509, br_sha256_ID, &br_sha256_vtable);
    br_x509_minimal_set_hash(x509, br_sha384_ID, &br_sha384_vtable);
    br_x509_minimal_set_hash(x509, br_sha512_ID, &br_sha512_vtable);
    br_x509_minimal_set_rsa(x509, &br_rsa_i31_pkcs1_vrfy);
    br_x509_minimal_set_ecdsa(x509, &br_ec_prime_i31, &br_ecdsa_i31_vrfy_asn1);
    set_certificate_date(x509);
    br_ssl_engine_set_x509(engine, &x509->vtable);

    // one record's room, used for sending or receiving in turn (a request, then its answer)
    br_ssl_engine_set_buffer(engine, connection->buffer, sizeof(connection->buffer), 0);
}

// ---- turning BearSSL's errors into ours ---------------------------------------------------------

// What went wrong with the connection, as one of our codes: what the network said, if that was
// the cause, or else BearSSL's last error for the connection
static int error_of (const tls_connection_t* connection) {
    if (connection->network_error != 0) {
        return connection->network_error;
    }
    int error = br_ssl_engine_last_error(&connection->client.eng);
    switch (error) {
        case BR_ERR_X509_EXPIRED:
        case BR_ERR_X509_TIME_UNKNOWN:
            return TLS_ERR_EXPIRED;
        case BR_ERR_X509_BAD_SERVER_NAME:
            return TLS_ERR_NAME;
        case BR_ERR_X509_NOT_TRUSTED:
            return TLS_ERR_UNTRUSTED;
        default:
            break;
    }
    // the other certificate errors: the BR_ERR_X509_ codes run from 32 to 62
    if (error >= BR_ERR_X509_INVALID_VALUE && error <= BR_ERR_X509_NOT_TRUSTED) {
        return TLS_ERR_CERT;
    }
    // the server said no (an alert), or sent something that isn't TLS 1.2 or that AerOS can't use
    if (error >= BR_ERR_RECV_FATAL_ALERT || error == BR_ERR_UNSUPPORTED_VERSION ||
        error == BR_ERR_BAD_VERSION || error == BR_ERR_NO_RANDOM || error == BR_ERR_BAD_CIPHER_SUITE ||
        error == BR_ERR_BAD_PARAM) {
        return TLS_ERR_HANDSHAKE;
    }
    return TLS_ERR_BROKEN;
}

// ---- what tls.h offers --------------------------------------------------------------------------

// Make an encrypted connection (see tls.h)
int tls_open (int tcp_connection, const char* host, tls_connection_t** out) {
    tls_connection_t* connection = malloc(sizeof(tls_connection_t));
    if (connection == NULL) {
        return NET_ERR_SEND;
    }
    memset(connection, 0, sizeof(tls_connection_t));
    connection->tcp = tcp_connection;
    connection->wait_ms = HANDSHAKE_WAIT_MS;
    configure(connection);

    // BearSSL's own random number generator needs a seed: 32 bytes nobody can guess
    uint8_t seed[32];
    crypto_random(seed, sizeof(seed));
    br_ssl_engine_inject_entropy(&connection->client.eng, seed, sizeof(seed));
    memset(seed, 0, sizeof(seed));

    // start the handshake with this name (it is sent to the server so it knows which of its
    // sites we want, and the certificate is checked to be for it), and run it to the end
    br_ssl_client_reset(&connection->client, host, 0);
    br_sslio_init(&connection->io, &connection->client.eng, read_from_network, connection,
                  write_to_network, connection);
    TRACE("TLS handshake with %s", host);
    if (br_sslio_flush(&connection->io) < 0) {
        int error = error_of(connection);
        TRACE("TLS handshake with %s failed: %d (BearSSL error %d)", host, error,
              br_ssl_engine_last_error(&connection->client.eng));
        free(connection);
        return error;
    }
    *out = connection;
    return NET_OK;
}

// send data, encrypted (see tls.h)
int tls_send (tls_connection_t* connection, const void* data, size_t length) {
    if (br_sslio_write_all(&connection->io, data, length) < 0 || br_sslio_flush(&connection->io) < 0) {
        return error_of(connection);
    }
    return NET_OK;
}

// receive data, decrypted (see tls.h)
int tls_receive (tls_connection_t* connection, void* out, size_t size, uint32_t ms) {
    connection->wait_ms = ms;
    int got = br_sslio_read(&connection->io, out, size);
    if (got > 0) {
        return got;
    }
    // Nothing more to read. That is the server finishing, if it said so (a "close notify", which
    // is no error) or if it just closed the connection, as servers that mark the end of a page
    // that way do
    int error = br_ssl_engine_last_error(&connection->client.eng);
    if (error == BR_ERR_OK || (error == BR_ERR_IO && connection->server_closed)) {
        return 0;
    }
    return error_of(connection);
}

// say goodbye and forget the connection (see tls.h)
void tls_close (tls_connection_t* connection) {
    if (connection == NULL) {
        return;
    }
    // "close notify", so the server knows the page wasn't cut short by someone else. No answer
    // is waited for (that would be another round trip): the TCP connection closes next
    connection->wait_ms = 1;
    br_sslio_close(&connection->io);
    memset(connection, 0, sizeof(tls_connection_t));   // the keys were in it
    free(connection);
}

// what an error means (see tls.h)
const char* tls_strerror (int error) {
    switch (error) {
        case TLS_ERR_EXPIRED:   return "the site's certificate has expired or isn't valid yet (or AerOS's clock is wrong)";
        case TLS_ERR_NAME:      return "the site's certificate is for a different name: it may not be the site it claims to be";
        case TLS_ERR_UNTRUSTED: return "the site's certificate isn't from an authority AerOS trusts (self-signed, or an unknown one)";
        case TLS_ERR_CERT:      return "the site's certificate isn't valid";
        case TLS_ERR_HANDSHAKE: return "couldn't make a secure connection (the site needs something other than TLS 1.2 with ECDHE)";
        case TLS_ERR_BROKEN:    return "the secure connection failed: the data was damaged or cut";
        default:                return net_strerror(error);
    }
}
