// K75 clone firmware: the same line protocol as CIRCUITPY/code.py, over
// the vendor HID interface (if2) instead of a CDC data port. Host->device
// bytes arrive as 64B Output/Feature reports via EP0 SET_REPORT (there is
// no OUT pipe); device->host lines ride 64B interrupt-IN reports. Lines
// are newline-terminated; a report is zero-padded, so the host ignores
// NUL bytes when reassembling.
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hardware/watchdog.h"
#include "pico/bootrom.h"
#include "pico/stdlib.h"
#include "tusb.h"

#include "keymap.h"

enum { ITF_KEYBOARD = 0, ITF_MEDIA, ITF_VENDOR };

#define MOUSE_REPORT_ID 6 // mouse collection in if1's report descriptor

#define MAX_HELD 16
#define RX_LIMIT 2048
#define TX_SIZE 4096
#define WATCHDOG_MS 2000 // host silence while armed drops every hold
#define HW_WATCHDOG_MS 4000
#define RESET_DELAY_MS 100 // lets the maintenance ACK go out first

// ---- held state (press order) ----
static uint8_t held[MAX_HELD];
static int held_n = 0;
static uint32_t lease_at[MAX_HELD]; // ms deadline per held entry, 0 = none
static uint8_t mouse_held = 0;      // button bitmask
static uint32_t mouse_lease_at[8];  // per button bit

static bool kbd_dirty = false;
static bool mouse_dirty = false;
static int16_t mouse_dx = 0, mouse_dy = 0, mouse_wheel = 0;

static bool watchdog_armed = false; // arms once the host speaks v2
static uint32_t last_rx = 0;
static uint32_t reset_at = 0;       // maintenance: reset into BOOTSEL
static uint8_t led_state = 0;

// ---- rx line assembly ----
static uint8_t rx_buf[RX_LIMIT];
static int rx_len = 0;

// ---- tx byte queue (lines, newline-terminated) ----
static uint8_t tx_buf[TX_SIZE];
static int tx_len = 0;

static uint32_t now_ms(void) {
    return to_ms_since_boot(get_absolute_time());
}

static void tx_push(const char *s, int n) {
    if (n <= 0 || n > TX_SIZE - tx_len) return;
    memcpy(tx_buf + tx_len, s, n);
    tx_len += n;
}

static void tx_line(const char *s) {
    tx_push(s, strlen(s));
    tx_push("\n", 1);
}

static void pump_tx(void) {
    while (tx_len > 0 && tud_hid_n_ready(ITF_VENDOR)) {
        uint8_t rep[64] = {0};
        int n = tx_len < 64 ? tx_len : 64;
        memcpy(rep, tx_buf, n);
        if (!tud_hid_n_report(ITF_VENDOR, 0, rep, sizeof rep)) return;
        memmove(tx_buf, tx_buf + n, tx_len - n);
        tx_len -= n;
    }
}

// "ACK"/"NACK" + " <seq>" + (" <data>" when seq and data) — same shape as
// code.py's reply().
static void reply(bool ok, const char *seq, const char *data) {
    char line[600]; // longest is ACK <seq> + the ~350-byte keys list
    int n = snprintf(line, sizeof line, "%s%s%s%s%s\n", ok ? "ACK" : "NACK",
                     seq[0] ? " " : "", seq,
                     (seq[0] && data && data[0]) ? " " : "",
                     (seq[0] && data) ? data : "");
    tx_push(line, n);
}

// ---- reports to the host PC ----

static void send_kbd(void) {
    uint8_t mod = 0, keys[6] = {0};
    int n = 0;
    for (int i = 0; i < held_n; i++) {
        uint8_t kc = held[i];
        if (kc >= 0xE0 && kc <= 0xE7) {
            mod |= (uint8_t)(1 << (kc - 0xE0));
        } else if (n < 6) {
            keys[n++] = kc;
        }
    }
    tud_hid_n_keyboard_report(ITF_KEYBOARD, 0, mod, keys);
}

static int8_t clamp8(int16_t v) {
    return (int8_t)(v > 127 ? 127 : v < -127 ? -127 : v);
}

static void send_mouse(void) {
    // A report carries at most +-127 per axis; the remainder rides the
    // next poll rather than being clipped away.
    int8_t rep[4] = {
        (int8_t)mouse_held, clamp8(mouse_dx), clamp8(mouse_dy), clamp8(mouse_wheel),
    };
    if (tud_hid_n_report(ITF_MEDIA, MOUSE_REPORT_ID, rep, sizeof rep)) {
        mouse_dx -= rep[1];
        mouse_dy -= rep[2];
        mouse_wheel -= rep[3];
        mouse_dirty = mouse_dx || mouse_dy || mouse_wheel;
    }
}

static void pump_hid(void) {
    if (kbd_dirty && tud_hid_n_ready(ITF_KEYBOARD)) {
        send_kbd();
        kbd_dirty = false;
    }
    if (mouse_dirty) send_mouse();
}

// ---- held bookkeeping ----

static int held_index(uint8_t kc) {
    for (int i = 0; i < held_n; i++)
        if (held[i] == kc) return i;
    return -1;
}

static void press_key(uint8_t kc) {
    if (held_index(kc) < 0 && held_n < MAX_HELD) {
        held[held_n] = kc;
        lease_at[held_n] = 0;
        held_n++;
    }
    kbd_dirty = true;
}

static void release_key(uint8_t kc) {
    int i = held_index(kc);
    if (i >= 0) {
        memmove(&held[i], &held[i + 1], held_n - i - 1);
        memmove(&lease_at[i], &lease_at[i + 1], (held_n - i - 1) * sizeof(uint32_t));
        held_n--;
    }
    kbd_dirty = true;
}

static int mouse_bit_index(uint8_t bit) { // 1->0, 2->1, 4->2
    return bit >> 1;
}

static void press_mouse(uint8_t bit) {
    if (mouse_held & bit) return;
    mouse_held |= bit;
    mouse_lease_at[mouse_bit_index(bit)] = 0;
    mouse_dirty = true;
}

static void release_mouse(uint8_t bit) {
    mouse_held &= (uint8_t)~bit;
    mouse_lease_at[mouse_bit_index(bit)] = 0;
    mouse_dirty = true;
}

static void release_everything(void) {
    held_n = 0;
    mouse_held = 0;
    memset(lease_at, 0, sizeof lease_at);
    memset(mouse_lease_at, 0, sizeof mouse_lease_at);
    kbd_dirty = true;
    mouse_dirty = true;
    mouse_dx = mouse_dy = mouse_wheel = 0;
}

static void expire_leases(uint32_t now) {
    for (int i = held_n - 1; i >= 0; i--) {
        if (lease_at[i] && (int32_t)(now - lease_at[i]) >= 0) {
            lease_at[i] = 0;
            int idx = i; // release_key shifts the array
            release_key(held[idx]);
        }
    }
    for (int b = 0; b < 3; b++) {
        uint8_t bit = (uint8_t)(1 << b);
        if ((mouse_held & bit) && mouse_lease_at[b] &&
            (int32_t)(now - mouse_lease_at[b]) >= 0) {
            release_mouse(bit);
        }
    }
}

static void set_lease_key(uint8_t kc, char **parts, int nparts) {
    int i = held_index(kc);
    if (i < 0) return;
    if (nparts >= 5 && parts[4][0] && strspn(parts[4], "0123456789 ") == strlen(parts[4])) {
        lease_at[i] = now_ms() + (uint32_t)atoi(parts[4]);
    } else {
        lease_at[i] = 0;
    }
}

static void set_lease_mouse(uint8_t bit, char **parts, int nparts) {
    int b = mouse_bit_index(bit);
    if (nparts >= 5 && parts[4][0] && strspn(parts[4], "0123456789 ") == strlen(parts[4])) {
        mouse_lease_at[b] = now_ms() + (uint32_t)atoi(parts[4]);
    } else {
        mouse_lease_at[b] = 0;
    }
}

// ---- held / keys answers ----

static int cmp_str(const void *a, const void *b) {
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

static void held_names(char *out, int cap) {
    const char *names[MAX_HELD + 3];
    char num[MAX_HELD + 3][16];
    int n = 0;
    for (int i = 0; i < held_n && n < MAX_HELD + 3; i++) {
        const char *s = key_name(held[i]);
        if (!s) {
            snprintf(num[n], sizeof num[n], "%u", held[i]);
            s = num[n];
        }
        names[n++] = s;
    }
    for (int b = 0; b < 3 && n < MAX_HELD + 3; b++) {
        uint8_t bit = (uint8_t)(1 << b);
        if (mouse_held & bit) {
            const char *m = mouse_name(bit);
            snprintf(num[n], sizeof num[n], "mouse:%s", m ? m : "?");
            names[n] = num[n];
            n++;
        }
    }
    qsort(names, n, sizeof names[0], cmp_str);
    int used = 0;
    for (int i = 0; i < n && used < cap - 1; i++) {
        used += snprintf(out + used, cap - used, "%s%s", i ? "|" : "", names[i]);
    }
}

static void all_keys(char *out, int cap) {
    const char *names[96];
    int n = KEY_MAP_LEN < 96 ? KEY_MAP_LEN : 96;
    for (int i = 0; i < n; i++) names[i] = KEY_MAP[i].name;
    qsort(names, n, sizeof names[0], cmp_str);
    int used = 0;
    for (int i = 0; i < n && used < cap - 1; i++) {
        used += snprintf(out + used, cap - used, "%s%s", i ? "|" : "", names[i]);
    }
}

// ---- command execution (port of code.py run()/handle()) ----

static bool run_parts(char **parts, int nparts) {
    if (nparts == 2 && strcmp(parts[0], "hid") == 0 &&
        strcmp(parts[1], "release_all") == 0) {
        release_everything();
        return true;
    }
    if (nparts >= 4 && strcmp(parts[0], "hid") == 0) {
        const char *kind = parts[1], *action = parts[2], *name = parts[3];
        if (strcmp(kind, "key") == 0) {
            int kc = keycode_of(name);
            if (kc < 0) return false;
            if (strcmp(action, "down") == 0) {
                press_key((uint8_t)kc);
                set_lease_key((uint8_t)kc, parts, nparts);
                return true;
            }
            if (strcmp(action, "up") == 0) {
                release_key((uint8_t)kc);
                return true;
            }
            return false;
        }
        if (strcmp(kind, "mouse") == 0) {
            int bit = mouse_bit_of(name);
            if (bit < 0) return false;
            if (strcmp(action, "down") == 0) {
                press_mouse((uint8_t)bit);
                set_lease_mouse((uint8_t)bit, parts, nparts);
                return true;
            }
            if (strcmp(action, "up") == 0) {
                release_mouse((uint8_t)bit);
                return true;
            }
            return false;
        }
        if (strcmp(kind, "move") == 0) {
            int dx = atoi(parts[2]), dy = atoi(parts[3]);
            if (dx || dy) {
                mouse_dx += dx;
                mouse_dy += dy;
                mouse_dirty = true;
            }
            return true;
        }
        if (strcmp(kind, "scroll") == 0) {
            int dy = atoi(parts[3]);
            if (dy) {
                mouse_wheel -= dy;
                mouse_dirty = true;
            }
            return true;
        }
        return false;
    }
    if (nparts == 2) { // legacy: down|<name> / up|<name>
        int kc = keycode_of(parts[1]);
        if (kc >= 0 && strcmp(parts[0], "down") == 0) {
            press_key((uint8_t)kc);
            int i = held_index((uint8_t)kc);
            if (i >= 0) lease_at[i] = 0;
            return true;
        }
        if (kc >= 0 && strcmp(parts[0], "up") == 0) {
            release_key((uint8_t)kc);
            return true;
        }
    }
    return false;
}

static void handle_line(char *line) {
    if (strcmp(line, "ka") == 0) {
        watchdog_armed = true;
        return;
    }
    if (!line[0]) return;

    char seq[16] = {0};
    char *colon = strchr(line, ':');
    if (colon && colon != line) {
        bool digits = true;
        for (char *p = line; p < colon; p++)
            if (!isdigit((unsigned char)*p)) digits = false;
        if (digits && colon - line < (int)sizeof seq) {
            memcpy(seq, line, colon - line);
            memmove(line, colon + 1, strlen(colon + 1) + 1);
            watchdog_armed = true;
        }
    }

    char *parts[8];
    int nparts = 0;
    for (char *tok = strtok(line, "|"); tok && nparts < 8;
         tok = strtok(NULL, "|")) {
        parts[nparts++] = tok;
    }

    if (nparts == 2 && strcmp(parts[0], "hid") == 0 &&
        strcmp(parts[1], "held") == 0) {
        char data[256];
        held_names(data, sizeof data);
        reply(true, seq, data);
        return;
    }
    if (nparts == 2 && strcmp(parts[0], "hid") == 0 &&
        strcmp(parts[1], "keys") == 0) {
        char data[512];
        all_keys(data, sizeof data);
        reply(true, seq, data);
        return;
    }
    if (nparts >= 1 && nparts <= 2 && strcmp(parts[0], "hello") == 0) {
        tx_line("PICO_READY v2");
        return;
    }
    if (nparts == 1 && strcmp(parts[0], "maintenance") == 0) {
        release_everything();
        reply(true, seq, "");
        pump_tx();
        reset_at = now_ms() + RESET_DELAY_MS;
        return;
    }

    bool handled = run_parts(parts, nparts);
    reply(handled, seq, "");
}

static void feed_rx(const uint8_t *buf, int n) {
    last_rx = now_ms();
    for (int i = 0; i < n; i++) {
        uint8_t c = buf[i];
        if (c == 0) continue; // zero padding inside reports
        if (c == '\n') {
            rx_buf[rx_len] = 0;
            char line[256];
            int m = rx_len < (int)sizeof line - 1 ? rx_len : (int)sizeof line - 1;
            memcpy(line, rx_buf, m);
            line[m] = 0;
            rx_len = 0;
            // trim
            char *s = line;
            while (*s == ' ' || *s == '\r' || *s == '\t') s++;
            char *e = s + strlen(s);
            while (e > s && (e[-1] == ' ' || e[-1] == '\r' || e[-1] == '\t')) *--e = 0;
            for (char *p = s; *p; p++) *p = (char)tolower((unsigned char)*p);
            handle_line(s);
        } else if (rx_len < RX_LIMIT - 1) {
            rx_buf[rx_len++] = c;
        } else {
            rx_len = 0; // overlong line with no newline: drop
        }
    }
}

// ---- TinyUSB callbacks ----

void tud_mount_cb(void) {
    tx_line("PICO_READY v2");
}

void tud_umount_cb(void) {
    release_everything();
    rx_len = 0;
    watchdog_armed = false;
}

void tud_suspend_cb(bool remote_wakeup_en) {
    (void)remote_wakeup_en;
    release_everything();
}

// SET_REPORT lands here for both the LED output report on if0 and the
// vendor channel's Output/Feature reports on if2 (no OUT endpoint).
void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id,
                           hid_report_type_t report_type,
                           uint8_t const *buffer, uint16_t bufsize) {
    (void)report_id;
    if (instance == ITF_KEYBOARD && report_type == HID_REPORT_TYPE_OUTPUT) {
        if (bufsize > 0) led_state = buffer[0];
        return;
    }
    if (instance == ITF_VENDOR &&
        (report_type == HID_REPORT_TYPE_OUTPUT ||
         report_type == HID_REPORT_TYPE_FEATURE)) {
        feed_rx(buffer, bufsize);
    }
}

uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id,
                               hid_report_type_t report_type,
                               uint8_t *buffer, uint16_t reqlen) {
    (void)report_id;
    if (instance == ITF_VENDOR && report_type == HID_REPORT_TYPE_FEATURE) {
        char data[64];
        held_names(data, sizeof data);
        int n = strlen(data) < reqlen ? (int)strlen(data) : reqlen;
        memcpy(buffer, data, n);
        return (uint16_t)n;
    }
    (void)instance;
    (void)report_type;
    (void)buffer;
    (void)reqlen;
    return 0;
}

int main(void) {
    release_everything();
    tusb_init();
    if (HW_WATCHDOG_MS) watchdog_enable(HW_WATCHDOG_MS, true);
    last_rx = now_ms();
    while (true) {
        tud_task();
        uint32_t now = now_ms();
        if (reset_at && (int32_t)(now - reset_at) >= 0) {
            pump_tx();
            reset_usb_boot(0, 0);
        }
        if (watchdog_armed && (held_n || mouse_held) &&
            (int32_t)(now - last_rx) > WATCHDOG_MS) {
            release_everything();
        }
        expire_leases(now);
        pump_hid();
        pump_tx();
        if (HW_WATCHDOG_MS) watchdog_update();
    }
}
