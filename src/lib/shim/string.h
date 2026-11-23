// The C decoders AerOS uses (minimp3 and pl_mpeg) include <string.h>. The kernel has no C
// library, so they get AerOS's own instead: the Makefile adds this folder to the include path
// for lib/mp3.c, lib/mpg.c and BearSSL (lib/bearssl) only
#include "../aer_string.h"

// BearSSL (lib/bearssl) measures text with strlen: AerOS calls it slen
#define strlen slen
