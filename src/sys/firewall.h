/*
    firewall.h - nothing goes in or out of the network unless you have allowed it

    The firewall is always on, and starts with no rules: everything is blocked, both ways. A rule
    allows one other computer (an address, or a DNS name looked up when the rule is made) on one
    port, with a password both ends know:

        fw allow 10.0.2.2 5000          asks for the password, then allows 10.0.2.2 port 5000
        fw allow example.com 5000       looks example.com up first (see "DNS" in sys/net.h)
        fw                              lists the rules
        fw del 1                        removes the first one

    What a rule allows:

        sending       a sealed message (sys/netseal.h) to that address, on that port
        receiving     a sealed message from that address, arriving on that port, and only if it
                      was sealed with the rule's password. Anything else is thrown away unread

    So a computer with no rule can't send AerOS anything, and a computer with a rule still has
    to know the password.

    WEB RULES are the other kind, for plain TCP: talking to a web server (sys/http.h), which
    knows nothing of seals. They have no password, and allow only that:

        fw web example.com              allows TCP connections to example.com, port 80
        fw web 10.0.2.2 8080            ...or to an address, on another port

    What a web rule allows:

        AerOS opening a TCP connection to that computer and port (sys/tcp.h), and the answers
        coming back on that connection. It doesn't let anything connect to AerOS: that is a
        serve rule's job

    SERVE RULES are the third kind: they let other computers connect to AerOS, to a program
    serving web pages, say (tcplisten in AerLang). Plain TCP, no password, as for web rules:

        fw serve 8080                   anyone may connect to AerOS's port 8080
        fw serve 8080 10.0.2.2          ...or only 10.0.2.2 may

    What a serve rule allows:

        a program listening on that port of AerOS's own (sys/tcp.h, tcp_listen), and other
        computers (anyone, or the one given) connecting to it there. A program can't listen on
        a port with no serve rule, and can't make a rule itself: only you, at the prompt, can

    The kinds never stand in for each other: a web rule doesn't allow sealed messages (it has
    no key), a rule with a password doesn't allow plain TCP, and only a serve rule lets anything
    connect to AerOS. Plain TCP isn't private, so anything sent over it can be read on the way:
    AerOS says so when a web or serve rule is made.

    The rules are kept in /acc/firewall, one a line, with each rule's shared key (made from its
    password, sys/netseal.h). /acc/ is the one folder no program can ever read or change
    (sys/perm.h), so programs can't open the firewall, or read the keys. Anyone at the
    terminal can, as the terminal is trusted.
*/
#ifndef FIREWALL_H
#define FIREWALL_H

#include <stdint.h>
#include <stdbool.h>
#include "netseal.h"

#define FW_RULES_MAX 64   // the most rules there can be (a web page and its pictures can need several)
#define FW_NAME_MAX  64   // the longest DNS name a rule can be made for, with its '\0'

typedef struct {
    uint32_t address;               // the other computer's IPv4 address (10.0.2.2 is 0x0A000202);
                                    // for a serve rule, 0 means any computer
    uint16_t port;                  // the port, the same at both ends (a serve rule: AerOS's own)
    char name[FW_NAME_MAX];         // the DNS name it was allowed as, or "" for an address
    bool web;                       // a web rule: plain TCP, with no key (see WEB RULES above)
    bool serve;                     // a serve rule: others may connect to AerOS (SERVE RULES)
    uint8_t key[NETSEAL_KEY_SIZE];  // the shared key, made from the password (sealed rules only)
} fw_rule_t;

// the sealed or web rule that allows this address and port, NULL if there is none (so it is
// blocked). Serve rules are never found here: they are about AerOS's own ports (fw_serves)
const fw_rule_t* fw_find (uint32_t address, uint16_t port);

// may `address` connect to AerOS's own port `port`? (a serve rule for that port, for any
// computer or that one)
bool fw_serves (uint32_t address, uint16_t port);

// is there a serve rule for AerOS's port `port` at all? (tcp_listen checks, before listening)
bool fw_port_served (uint16_t port);

// add a serve rule (FW SERVE): others may connect to AerOS's port `port`, from `address` only
// (or from anyone, for 0). Replaces a serve rule for the same port and address. False if there
// are FW_RULES_MAX already
bool fw_allow_serve (uint32_t address, uint16_t port);

// the rule for a DNS name and port (as it was allowed), NULL if there is none
const fw_rule_t* fw_find_name (const char* name, uint16_t port);

// add a rule (or replace the one for the same address and port) and save the rules.
// name is "" for a rule made for an address. False if there are FW_RULES_MAX already
bool fw_allow (uint32_t address, uint16_t port, const char* name, const uint8_t key[NETSEAL_KEY_SIZE]);

// add a web rule (FW WEB): plain TCP to this address and port, no password. Replaces a rule for
// the same address and port, as fw_allow does. False if there are FW_RULES_MAX already
bool fw_allow_web (uint32_t address, uint16_t port, const char* name);

// remove a rule (numbered from 1, the order fw_rule lists them in) and save. False if no such rule
bool fw_remove (int number);

// how many rules there are, and rule number n (from 1), NULL past the end
int fw_count (void);
const fw_rule_t* fw_rule (int number);

// is a sealed message allowed on this port at all? (if not, none can ever arrive on it).
// Web and serve rules don't count: they are for plain TCP, not sealed messages
bool fw_port_open (uint16_t port);

// the address to use for `host` on `port`: host is an address ("10.0.2.2"), or a DNS name a
// rule was made for. Names are never looked up here, only when a rule is made (FW ALLOW), so
// false if no rule was made for that name
bool fw_resolve (const char* host, uint16_t port, uint32_t* address);

#endif /* FIREWALL_H */
