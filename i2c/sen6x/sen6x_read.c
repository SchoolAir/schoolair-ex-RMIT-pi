/*
 * sen6x_read.c — one-shot sensor read for the SchoolAir gateway.
 *
 * Usage:
 *   sen6x_read          Fast path: assumes continuous measurement is already
 *                       running (started by `sen6x_read --init` at boot via
 *                       sen6x.service).  Reads one sample, prints JSON to
 *                       stdout, exits.
 *
 *   sen6x_read --init   Slow path: full initialisation sequence (device reset,
 *                       start_continuous_measurement, wait for first valid
 *                       sample).  Run once at boot by sen6x.service before
 *                       the telemetry service starts.
 *
 * Exit codes:
 *   0   Success — valid JSON on stdout.
 *   1   I2C / sensor communication error.
 *   2   Sensor not ready after all retries.
 *
 * JSON output:
 *   {"sen6x": {"measured_at": "2026-06-24T12:05:01Z", "temp": 22.50, ...}}
 *
 * The "measured_at" field carries the C-side wall-clock time (UTC) at the
 * moment the I2C read returned, which is more accurate than the Python-side
 * "recorded_at" timestamp that is set when the subprocess exits.  Both are
 * preserved in the measurement buffer so the server can see any gap.
 */

#include "sen63c_i2c.h"
#include "sen65_i2c.h"
#include "sensirion_common.h"
#include "sensirion_i2c_hal.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* Aliases matching sen6x_d.c so both files share the same naming convention */
#define sensirion_hal_sleep_us             sensirion_i2c_hal_sleep_usec
#define sen6x_init                         sen63c_init
#define sen6x_device_reset                 sen63c_device_reset
#define sen6x_get_product_name             sen63c_get_product_name
#define sen6x_start_continuous_measurement sen63c_start_continuous_measurement


/* ── Types ──────────────────────────────────────────────────────────────── */

typedef enum { SEN63C, SEN65, SENSOR_UNKNOWN } sensor_type_t;

typedef struct {
    float    pm10, pm25, pm40, pm100;
    float    temperature, humidity;
    union {
        struct { uint16_t co2;      } sen63c_data;
        struct { float    voc, nox; } sen65_data;
    } sensor_specific;
} sensor_reading_t;


/* ── Helpers ────────────────────────────────────────────────────────────── */

static sensor_type_t detect_sensor_type(void) {
    int8_t name[32] = {0};
    if (sen63c_get_product_name(name, 32) != NO_ERROR)
        return SENSOR_UNKNOWN;
    if (strstr((char *)name, "SEN63")) return SEN63C;
    if (strstr((char *)name, "SEN65")) return SEN65;
    return SENSOR_UNKNOWN;
}

static int16_t read_values(sensor_reading_t *r, sensor_type_t type) {
    if (type == SEN63C)
        return sen63c_read_measured_values(
            &r->pm10, &r->pm25, &r->pm40, &r->pm100,
            &r->humidity, &r->temperature,
            &r->sensor_specific.sen63c_data.co2);
    return sen65_read_measured_values(
        &r->pm10, &r->pm25, &r->pm40, &r->pm100,
        &r->humidity, &r->temperature,
        &r->sensor_specific.sen65_data.voc,
        &r->sensor_specific.sen65_data.nox);
}

/* SEN63C signals "no data yet" with co2 == 32767.
 * SEN65 has no equivalent placeholder — a successful read is a valid read. */
static int reading_is_ready(const sensor_reading_t *r, sensor_type_t type) {
    if (type == SEN63C)
        return r->sensor_specific.sen63c_data.co2 != 32767;
    return 1;
}

static void print_json(const sensor_reading_t *r, sensor_type_t type) {
    char ts[25];
    time_t now = time(NULL);
    strftime(ts, sizeof(ts), "%Y-%m-%dT%H:%M:%SZ", gmtime(&now));

    printf("{\"sen6x\": {\"measured_at\": \"%s\", "
           "\"temp\": %.2f, \"humidity\": %.2f, ",
           ts, r->temperature, r->humidity);

    if (type == SEN63C)
        printf("\"co2\": %u, ", r->sensor_specific.sen63c_data.co2);
    else
        printf("\"voc\": %.1f, \"nox\": %.1f, ",
               r->sensor_specific.sen65_data.voc,
               r->sensor_specific.sen65_data.nox);

    printf("\"pm10\": %.1f, \"pm25\": %.1f, \"pm40\": %.1f, \"pm100\": %.1f}}\n",
           r->pm10, r->pm25, r->pm40, r->pm100);
}


/* ── --init path ────────────────────────────────────────────────────────── */

/* Full initialisation: reset → settle → start continuous measurement →
 * wait up to 10 × 2 s for first valid sample.  Mirrors the startup sequence
 * in sen6x_d.c so both binaries behave identically on first boot. */
static int do_init(sensor_type_t type) {
    int16_t error;

    error = sen6x_device_reset();
    if (error != NO_ERROR) {
        fprintf(stderr, "[sen6x_read] device_reset failed: %d\n", error);
        return 1;
    }
    sensirion_hal_sleep_us(1200000UL);   /* 1.2 s datasheet settling time */

    error = sen6x_start_continuous_measurement();
    if (error != NO_ERROR) {
        fprintf(stderr, "[sen6x_read] start_continuous_measurement failed: %d\n", error);
        return 1;
    }

    sensor_reading_t r;
    for (int i = 0; i < 10; i++) {
        error = read_values(&r, type);
        if (error == NO_ERROR && reading_is_ready(&r, type)) {
            print_json(&r, type);
            return 0;
        }
        sensirion_hal_sleep_us(2000000UL);   /* 2 s */
    }

    fprintf(stderr, "[sen6x_read] no valid reading after init (20 s)\n");
    return 2;
}


/* ── Fast read path ─────────────────────────────────────────────────────── */

/* Assumes continuous measurement is already running.  Three retries with a
 * 500 ms gap cover the rare case of calling exactly between two internal
 * measurement cycles (~1 s on SEN63C).  Exits non-zero on failure so
 * sensor.py raises RuntimeError and the calling read is skipped cleanly. */
static int do_read(sensor_type_t type) {
    sensor_reading_t r;
    int16_t error = NO_ERROR;

    for (int i = 0; i < 3; i++) {
        error = read_values(&r, type);
        if (error == NO_ERROR && reading_is_ready(&r, type)) {
            print_json(&r, type);
            return 0;
        }
        if (error != NO_ERROR)
            fprintf(stderr, "[sen6x_read] I2C error %d (attempt %d/3)\n", error, i + 1);
        else
            fprintf(stderr, "[sen6x_read] not ready — co2=32767 (attempt %d/3)\n", i + 1);
        sensirion_hal_sleep_us(500000UL);   /* 0.5 s */
    }

    if (error != NO_ERROR) {
        fprintf(stderr, "[sen6x_read] I2C communication failed\n");
        return 1;
    }
    fprintf(stderr, "[sen6x_read] sensor not ready — is sen6x.service running?\n");
    return 2;
}


/* ── Entry point ────────────────────────────────────────────────────────── */

int main(int argc, char *argv[]) {
    int init_mode = (argc > 1 && strcmp(argv[1], "--init") == 0);

    sensirion_i2c_hal_init();
    sen6x_init(0x6b);

    sensor_type_t type = detect_sensor_type();
    if (type == SENSOR_UNKNOWN) {
        fprintf(stderr, "[sen6x_read] cannot detect sensor (I2C error or sensor absent)\n");
        return 1;
    }
    if (type == SEN65)
        sen65_init(0x6b);

    return init_mode ? do_init(type) : do_read(type);
}
