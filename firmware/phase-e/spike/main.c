// Phase E spike: a K75-shaped device with a vendor HID channel. Every
// 64-byte report on the channel is echoed back, so the host can time round
// trips. 'K' <hid keycode> presses that key on the keyboard interface and
// 'U' releases it, to prove the keyboard works end to end.
#include <string.h>

#include "pico/stdlib.h"
#include "tusb.h"

#define ITF_KEYBOARD 0
#define ITF_VENDOR 1

static uint8_t pending[64];
static volatile bool have_pending = false;
static uint8_t kbd_key = 0;
static volatile int kbd_action = 0; // 1 = press, 2 = release

void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize) {
    (void)report_id;
    (void)report_type;
    if (instance != ITF_VENDOR || bufsize == 0) return;
    if (buffer[0] == 'K' && bufsize > 1) {
        kbd_key = buffer[1];
        kbd_action = 1;
    } else if (buffer[0] == 'U') {
        kbd_action = 2;
    }
    memset(pending, 0, sizeof pending);
    memcpy(pending, buffer, bufsize > 64 ? 64 : bufsize);
    have_pending = true;
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t report_type,
                               uint8_t *buffer, uint16_t reqlen) {
    (void)instance;
    (void)report_id;
    (void)report_type;
    (void)buffer;
    (void)reqlen;
    return 0;
}

int main(void) {
    tusb_init();
    while (true) {
        tud_task();
        if (kbd_action && tud_hid_n_ready(ITF_KEYBOARD)) {
            uint8_t keys[6] = {0};
            if (kbd_action == 1) keys[0] = kbd_key;
            tud_hid_n_keyboard_report(ITF_KEYBOARD, 0, 0, keys);
            kbd_action = 0;
        }
        if (have_pending && tud_hid_n_ready(ITF_VENDOR)) {
            tud_hid_n_report(ITF_VENDOR, 0, pending, sizeof pending);
            have_pending = false;
        }
    }
}
