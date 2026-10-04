#include <string.h>

#include "keymap.h"

const KeyName KEY_MAP[] = {
    {"a", 0x04}, {"b", 0x05}, {"c", 0x06}, {"d", 0x07}, {"e", 0x08},
    {"f", 0x09}, {"g", 0x0A}, {"h", 0x0B}, {"i", 0x0C}, {"j", 0x0D},
    {"k", 0x0E}, {"l", 0x0F}, {"m", 0x10}, {"n", 0x11}, {"o", 0x12},
    {"p", 0x13}, {"q", 0x14}, {"r", 0x15}, {"s", 0x16}, {"t", 0x17},
    {"u", 0x18}, {"v", 0x19}, {"w", 0x1A}, {"x", 0x1B}, {"y", 0x1C},
    {"z", 0x1D},

    {"1", 0x1E}, {"2", 0x1F}, {"3", 0x20}, {"4", 0x21}, {"5", 0x22},
    {"6", 0x23}, {"7", 0x24}, {"8", 0x25}, {"9", 0x26}, {"0", 0x27},

    {"f1", 0x3A}, {"f2", 0x3B}, {"f3", 0x3C}, {"f4", 0x3D},
    {"f5", 0x3E}, {"f6", 0x3F}, {"f7", 0x40}, {"f8", 0x41},
    {"f9", 0x42}, {"f10", 0x43}, {"f11", 0x44}, {"f12", 0x45},

    {"enter", 0x28}, {"esc", 0x29}, {"backspace", 0x2A}, {"tab", 0x2B},
    {"space", 0x2C}, {"-", 0x2D}, {"=", 0x2E}, {"[", 0x2F}, {"]", 0x30},
    {"\\", 0x31}, {";", 0x33}, {"'", 0x34}, {"`", 0x35}, {",", 0x36},
    {".", 0x37}, {"/", 0x38},

    {"caps lock", 0x39},
    {"shift", 0xE1}, {"ctrl", 0xE0}, {"alt", 0xE2},
    {"cmd", 0xE3}, {"windows", 0xE3},
    {"right shift", 0xE5}, {"right ctrl", 0xE4}, {"right alt", 0xE6},

    {"print screen", 0x46}, {"scroll lock", 0x47}, {"pause", 0x48},
    {"insert", 0x49}, {"home", 0x4A}, {"page up", 0x4B}, {"delete", 0x4C},
    {"end", 0x4D}, {"page down", 0x4E},
    {"right", 0x4F}, {"left", 0x50}, {"down", 0x51}, {"up", 0x52},
};
const int KEY_MAP_LEN = sizeof(KEY_MAP) / sizeof(KEY_MAP[0]);

const MouseName MOUSE_MAP[] = {
    {"left", 0x01}, {"right", 0x02}, {"middle", 0x04},
};
const int MOUSE_MAP_LEN = sizeof(MOUSE_MAP) / sizeof(MOUSE_MAP[0]);

const char *key_name(uint8_t keycode) {
    for (int i = 0; i < KEY_MAP_LEN; i++)
        if (KEY_MAP[i].keycode == keycode) return KEY_MAP[i].name;
    return NULL;
}

const char *mouse_name(uint8_t bit) {
    for (int i = 0; i < MOUSE_MAP_LEN; i++)
        if (MOUSE_MAP[i].bit == bit) return MOUSE_MAP[i].name;
    return NULL;
}

int keycode_of(const char *name) {
    for (int i = 0; i < KEY_MAP_LEN; i++)
        if (strcmp(KEY_MAP[i].name, name) == 0) return KEY_MAP[i].keycode;
    return -1;
}

int mouse_bit_of(const char *name) {
    for (int i = 0; i < MOUSE_MAP_LEN; i++)
        if (strcmp(MOUSE_MAP[i].name, name) == 0) return MOUSE_MAP[i].bit;
    return -1;
}
