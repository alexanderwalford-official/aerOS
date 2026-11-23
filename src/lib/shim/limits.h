// BearSSL (lib/bearssl) includes <limits.h>, which on a normal computer comes with the C
// library, and the kernel has none. The one thing it takes from it is ULONG_MAX; the compiler
// itself knows how big a long is
#ifndef SHIM_LIMITS_H
#define SHIM_LIMITS_H

#define ULONG_MAX (__LONG_MAX__ * 2UL + 1UL)

#endif
