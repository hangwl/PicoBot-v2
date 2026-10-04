#ifndef KEYMAP_H_
#define KEYMAP_H_

#include <stdint.h>
#include <stdbool.h>

// Same names as CIRCUITPY/code.py KEY_MAP / core/src/keys.rs PICO_KEYS.
// Order matters for key_name(): the first name for a keycode wins, like
// Python's setdefault (LEFT_GUI reports "cmd", not "windows").
typedef struct {
    const char *name;
    uint8_t keycode; // USB HID usage (keyboard page)
} KeyName;

extern const KeyName KEY_MAP[];
extern const int KEY_MAP_LEN;

typedef struct {
    const char *name;
    uint8_t bit; // button bit in the mouse report
} MouseName;

extern const MouseName MOUSE_MAP[];
extern const int MOUSE_MAP_LEN;

const char *key_name(uint8_t keycode);   // NULL if unknown
const char *mouse_name(uint8_t bit);     // NULL if unknown
int keycode_of(const char *name);        // -1 if unknown
int mouse_bit_of(const char *name);      // -1 if unknown

#endif
