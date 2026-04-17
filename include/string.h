#ifndef _STRING_H
#define _STRING_H

#include "stddef.h"

void* memset(void* dst, int val, size_t n);
void* memcpy(void* dst, const void* src, size_t n);
void* memmove(void* dst, const void* src, size_t n);

#endif  // _STRING_H
