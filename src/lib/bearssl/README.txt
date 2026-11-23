BearSSL 0.6 (https://bearssl.org/, Thomas Pornin, MIT licence: see LICENSE), as used by
AerOS's HTTPS (sys/tls.c).

Only what a TLS 1.2 client needs is here, the rest of the release (server, TLS 1.0/1.1, CBC and
3DES, RSA key exchange, the tools and tests) is left out. The files are BearSSL's own and
unchanged, except src/config.h, which has a block at its end (marked "AerOS") that switches off
what AerOS has no operating system for: the time, the random seed and the CPU-specific code.
If BearSSL is updated, copy the same files over and keep that block.
