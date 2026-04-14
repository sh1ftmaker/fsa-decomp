// Four Swords Adventures - Standard Memory Functions
// Decompiled from the original DOL executable
//
// These match the Dolphin SDK standard implementations

#include <stddef.h>
#include <string.h>

/* memset - Fill memory block with value
 * Address: 0x8000540C
 * Size: 0x30 bytes
 */
void* memset(void* ptr, int value, size_t num) {
    unsigned char* p = (unsigned char*)ptr;
    unsigned char v = (unsigned char)value;

    while (num > 0) {
        *p++ = v;
        num--;
    }
    return ptr;
}

/* memcpy - Copy memory block
 * Address: 0x800054F4
 * Size: 0x50 bytes
 */
void* memcpy(void* dest, const void* src, size_t n) {
    unsigned char* d = (unsigned char*)dest;
    const unsigned char* s = (const unsigned char*)src;

    while (n > 0) {
        *d++ = *s++;
        n--;
    }
    return dest;
}
