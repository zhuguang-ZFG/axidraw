#
# Copyright 2023 Windell H. Oskay, Evil Mad Scientist Laboratories
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""
serial_utils.py

This module modularizes serial functions for multiple controller backends.

Part of the AxiDraw driver for Inkscape
https://github.com/evil-mad/AxiDraw

Requires Python 3.7 or newer.
"""

import re
import time
import gettext
from axidrawinternal.plot_utils_import import from_dependency_import
from axidrawinternal.axidraw_options import versions as ad_versions

ebb_serial = from_dependency_import('plotink.ebb_serial')  # https://github.com/evil-mad/plotink
ebb_motion = from_dependency_import('plotink.ebb_motion')

try:
    import serial
    from serial.tools import list_ports
except Exception:  # pragma: no cover
    serial = None
    list_ports = None


def _grbl_port_records():
    """Return serial port records ordered by likelihood of being a real controller."""
    if list_ports is None:
        return []
    port_records = list(list_ports.comports())

    def _priority(record):
        description = (getattr(record, "description", "") or "").lower()
        hwid = (getattr(record, "hwid", "") or "").lower()
        device = (getattr(record, "device", "") or "").lower()

        # Prefer Bluetooth serial first, then USB serial, and de-prioritize motherboard COM ports.
        is_bluetooth_serial = _is_bluetooth_serial_record(record)
        is_usb_serial = ("usb" in description) or ("usb" in hwid) or ("vid:pid" in hwid)
        is_builtin_uart = ("acpi" in hwid) or ("pnp" in hwid) or description.startswith("communication port")
        is_low_com_builtin = is_builtin_uart and device in ("com1", "com2")
        return (
            0 if is_bluetooth_serial else (1 if is_usb_serial else 2),
            1 if is_low_com_builtin else 0,
            device,
        )

    port_records.sort(key=_priority)
    return port_records


def _is_usb_serial_record(record):
    """Return True when the port appears to be an external USB serial adapter."""
    description = (getattr(record, "description", "") or "").lower()
    hwid = (getattr(record, "hwid", "") or "").lower()
    return ("usb" in description) or ("usb" in hwid) or ("vid:pid" in hwid)


def _is_bluetooth_serial_record(record):
    """Return True when the port appears to be a Bluetooth virtual COM port."""
    description = (getattr(record, "description", "") or "").lower()
    hwid = (getattr(record, "hwid", "") or "").lower()
    bluetooth_tokens = ("bluetooth", "bth", "rfcomm", "wireless")
    return any(token in description for token in bluetooth_tokens) or \
        any(token in hwid for token in bluetooth_tokens)


def _bluetooth_record_rank(record):
    """
    Rank Bluetooth serial records.
    Lower is better. Prefer ports that look like the active SPP channel and
    de-prioritize placeholder/empty RFCOMM endpoints such as LOCALMFG&0000.
    """
    hwid = (getattr(record, "hwid", "") or "").lower()
    description = (getattr(record, "description", "") or "").lower()
    device = (getattr(record, "device", "") or "").lower()
    score = 0
    if "localmfg&0000" in hwid:
        score += 50
    if "standard serial" in description or "标准串行" in description:
        score += 0
    if device.startswith("com"):
        try:
            score += int(device[3:]) / 1000.0
        except Exception:
            pass
    return score


def is_grbl(plot_status):
    """Return True when the active controller is Grbl/FluidNC style."""
    return getattr(plot_status, "controller", "grbl_esp32") == "grbl_esp32"


def _translation(options):
    """Return a gettext translation function for runtime messages."""
    return gettext.gettext


def _wanted_controller(options):
    return "grbl_esp32"

def connect(options, plot_status, message_fun, logger):
    """Connect to controller over USB serial."""
    return _connect_grbl(options, plot_status, message_fun, logger)


def _connect_ebb(options, plot_status, message_fun, logger):
    """Connect to EBB firmware over USB."""
    port_name = None
    if options.port_config == 1: # port_config value "1": Use first available AxiDraw.
        options.port = None
    if not options.port: # Try to connect to first available AxiDraw.
        plot_status.port = ebb_serial.openPort()
    elif str(type(options.port)) in (
            "<type 'str'>", "<type 'unicode'>", "<class 'str'>"):
        # This function may be passed a port name to open (and later close).
        options.port = str(options.port).strip('\"')
        port_name = options.port
        the_port = ebb_serial.find_named_ebb(options.port)
        plot_status.port = ebb_serial.testPort(the_port)
        options.port = None  # Clear this input, to ensure that we close the port later.
    else:
        # options.port may be a serial port object of type serial.serialposix.Serial.
        # In that case, interact with that given port object, and leave it open at the end.
        plot_status.port = options.port

    if plot_status.port is None:
        if port_name:
            message_fun('Failed to connect to AxiDraw ' + str(port_name))
        else:
            message_fun("Failed to connect to AxiDraw.")
        return False

    fw_version_string = ebb_serial.queryVersion(plot_status.port) # Full string, human readable
    fw_version_string = fw_version_string.split("Firmware Version ", 1)
    fw_version_string = fw_version_string[1]
    plot_status.fw_version = fw_version_string.strip() # For number comparisons
    plot_status.controller = "ebb"
    plot_status.grbl_settings = {}
    plot_status.grbl_status = None

    if port_name:
        logger.debug('Connected successfully to port: ' + str(port_name))
    else:
        logger.debug(" Connected successfully")
    return True


def _connect_grbl(options, plot_status, message_fun, logger):
    """Connect to Grbl/Grbl_ESP32 style firmware."""
    _ = _translation(options)
    if serial is None or list_ports is None:
        message_fun(_("PySerial is required for Grbl mode but is unavailable."))
        return False

    requested_baud = getattr(options, "grbl_baud_rate", 115200)
    try:
        baud_rate = int(float(requested_baud))
    except Exception:
        baud_rate = 115200
    if baud_rate < 300:
        message_fun("检测到异常波特率参数 {}，已自动回退到 115200。".format(requested_baud))
        baud_rate = 115200
    timeout_s = float(getattr(options, "grbl_command_timeout", 2.0))
    handshake_timeout_s = max(3.0, timeout_s)

    preferred_ports = []
    selected_port = None
    dropdown_port = getattr(options, "port_choice", "auto")
    discovered_records = _grbl_port_records()
    discovered_record_map = {item.device: item for item in discovered_records}
    discovered_ports = [item.device for item in discovered_records]
    bt_records = [item for item in discovered_records if _is_bluetooth_serial_record(item)]
    bt_records.sort(key=_bluetooth_record_rank)
    bt_discovered_ports = [item.device for item in bt_records]
    usb_discovered_ports = [item.device for item in discovered_records if _is_usb_serial_record(item)]
    last_error = None
    busy_ports = []
    handshake_fail_ports = []
    if options.port_config == 1:
        options.port = None
    if options.port:
        selected_port = str(options.port).strip('"')
        preferred_ports.append(selected_port)
        # Auto-fallback: prefer Bluetooth/USB serial ports, not motherboard COM ports like COM1.
        fallback_ports = bt_discovered_ports + [item for item in usb_discovered_ports if item not in bt_discovered_ports]
        fallback_ports = fallback_ports or discovered_ports
        preferred_ports.extend([item for item in fallback_ports if item != selected_port])
    else:
        if dropdown_port and str(dropdown_port).lower() != "auto":
            dropdown_port = str(dropdown_port).strip('"')
            preferred_ports.append(dropdown_port)
            fallback_ports = bt_discovered_ports + [item for item in usb_discovered_ports if item not in bt_discovered_ports]
            fallback_ports = fallback_ports or discovered_ports
        else:
            fallback_ports = bt_discovered_ports + [item for item in usb_discovered_ports if item not in bt_discovered_ports]
            fallback_ports = fallback_ports or discovered_ports
        preferred_ports.extend([item for item in fallback_ports if item not in preferred_ports])

    # If auto mode finds a CH340/USB serial controller, try it twice before giving up.
    if not preferred_ports and "COM3" in usb_discovered_ports:
        preferred_ports.append("COM3")

    for pass_index in range(3):
        for port_name in preferred_ports:
            port = None
            for _attempt in range(4):
                exc_text = None
                exc_obj = None
                try:
                    port = serial.Serial(
                        port=port_name,
                        baudrate=baud_rate,
                        timeout=0.20,
                        write_timeout=0.20)
                    break
                except Exception as exc:
                    exc_obj = exc
                    exc_text = f"{type(exc).__name__}: {exc}"
                    last_error = f"{port_name}: {exc_text}"
                    lower_text = str(exc).lower()
                    if ("access is denied" in lower_text) or ("permission" in lower_text):
                        if port_name not in busy_ports:
                            busy_ports.append(port_name)
                    time.sleep(0.20 if pass_index == 0 else 0.40)
            if port is None:
                continue

            try:
                # Many controllers reboot when serial opens; allow boot banner to appear.
                _grbl_prepare_after_open(port)
                ok, id_lines = _grbl_handshake_with_retries(
                    port,
                    timeout_s=handshake_timeout_s,
                    attempts=3 if pass_index < 2 else 4)
                if not ok:
                    if port_name not in handshake_fail_ports:
                        handshake_fail_ports.append(port_name)
                    last_error = f"{port_name}: handshake_failed"
                    try:
                        port.close()
                    except Exception:
                        pass
                    time.sleep(0.25 if pass_index == 0 else 0.45)
                    continue

                plot_status.port = port
                plot_status.controller = "grbl_esp32"
                plot_status.port_name = port_name
                plot_status.grbl_status = None
                plot_status.grbl_settings = {}
                grbl_reset_stream_state(plot_status, clear_io=True)
                plot_status.fw_version = _extract_grbl_version(id_lines)
                plot_status.grbl_axis_swap_xy = bool(getattr(options, "grbl_axis_swap_xy", False))
                plot_status.grbl_axis_invert_x = bool(getattr(options, "grbl_axis_invert_x", False))
                plot_status.grbl_axis_invert_y = bool(getattr(options, "grbl_axis_invert_y", False))
                record = discovered_record_map.get(port_name)
                plot_status.grbl_is_bluetooth = bool(record and _is_bluetooth_serial_record(record))

                if getattr(options, "grbl_auto_fetch", True) and not plot_status.grbl_is_bluetooth:
                    plot_status.grbl_settings = read_grbl_settings(plot_status, timeout_s=handshake_timeout_s)
                elif getattr(options, "grbl_auto_fetch", True) and plot_status.grbl_is_bluetooth:
                    message_fun("检测到蓝牙串口，已跳过自动读取全部固件设置，避免连接卡顿。")
                grbl_reset_stream_state(plot_status, clear_io=True)
                _grbl_drain_incoming(plot_status)

                logger.debug("Connected successfully to Grbl port: %s", port_name)
                return True
            except Exception as exc:
                last_error = f"{port_name}: {type(exc).__name__}: {exc}"
                try:
                    port.close()
                except Exception:
                    pass
        if pass_index < 2 and preferred_ports:
            time.sleep(0.70 if pass_index == 0 else 1.0)

    if selected_port:
        message_fun(_("Failed to connect to Grbl controller at the selected port."))
    else:
        message_fun(_("Failed to connect to any Grbl controller."))
    if busy_ports:
        message_fun("串口被占用：{}。请关闭串口监视器、其他绘图/调试程序后重试。".format(
            ", ".join(busy_ports)))
    elif handshake_fail_ports:
        message_fun("已找到串口但握手失败：{}。设备可能刚复位、固件忙碌，或被其他程序短暂占用。".format(
            ", ".join(handshake_fail_ports)))
    elif discovered_ports:
        message_fun("当前检测到的串口：{}。".format(", ".join(discovered_ports)))
    if last_error:
        message_fun("最近一次连接错误：{}。".format(last_error))
    return False


def sanitize_grbl_option_defaults(options, message_fun=None):
    """Normalize hidden Grbl UI parameters that Inkscape may truncate or coerce oddly."""
    def _emit(msg):
        if message_fun:
            message_fun(msg)

    try:
        slow_feed = float(getattr(options, "grbl_pen_down_slow_feed", 0.0))
    except Exception:
        slow_feed = 0.0
    if slow_feed < 0.0:
        _emit("检测到异常低速落笔速度参数 {}，已回退到 0 mm/min。".format(
            getattr(options, "grbl_pen_down_slow_feed", slow_feed)))
        options.grbl_pen_down_slow_feed = 0.0

    try:
        settle_ms = int(float(getattr(options, "grbl_pen_down_settle_ms", 0)))
    except Exception:
        settle_ms = 0
    if settle_ms < 0:
        _emit("检测到异常落笔缓冲参数 {}，已回退到 0 ms。".format(
            getattr(options, "grbl_pen_down_settle_ms", settle_ms)))
        options.grbl_pen_down_settle_ms = 0

    try:
        dir_mask = int(float(getattr(options, "grbl_set_dir_mask", -1)))
    except Exception:
        dir_mask = -1
    if dir_mask == 0:
        options.grbl_set_dir_mask = -1

    try:
        homing_mask = int(float(getattr(options, "grbl_set_homing_dir_mask", -1)))
    except Exception:
        homing_mask = -1
    if homing_mask == 0:
        options.grbl_set_homing_dir_mask = -1


def _grbl_handshake(port, timeout_s=2.0):
    """Return (ok, lines)."""
    lines = []
    try:
        reset_input_buffer(port)
        port.write(b"\r\n")
        port.flush()
        time.sleep(0.20)
        lines.extend(_read_available_lines(port))

        port.write(b"$I\n")
        port.flush()
        end = time.time() + timeout_s
        while time.time() < end:
            new_lines = _read_available_lines(port)
            if new_lines:
                lines.extend(new_lines)
            for line in new_lines:
                low = line.lower()
                if low.startswith("ok"):
                    if _contains_grbl_identity(lines):
                        return True, lines
                    break
                if low.startswith("error"):
                    return False, lines
            time.sleep(0.02)
        # Some Grbl_ESP32 builds return only "ok" for $I; fall back to status/settings probes.
        if _grbl_status_probe(port, timeout_s=max(0.5, timeout_s * 0.5)):
            return True, lines
        settings_ok, settings_lines = _grbl_settings_probe(port, timeout_s=max(0.8, timeout_s))
        if settings_ok:
            lines.extend(settings_lines)
            return True, lines
    except Exception:
        return False, lines
    return _contains_grbl_identity(lines), lines


def _grbl_prepare_after_open(port):
    """Settle controller after serial open; tolerate boards that auto-reset."""
    try:
        # Prevent DTR-induced reset loops on some USB bridges.
        port.dtr = False
    except Exception:
        pass
    try:
        reset_input_buffer(port)
    except Exception:
        pass
    time.sleep(0.18)
    # Drain any startup banner like: "Grbl ... ['$' for help]"
    _read_available_lines(port)


def grbl_reset_stream_state(plot_status, clear_io=False):
    """Reset tracked Grbl stream bookkeeping, optionally clearing serial buffers."""
    plot_status.grbl_pending_lengths = []
    plot_status.grbl_stream_error = None
    if clear_io and plot_status.port is not None:
        try:
            reset_input_buffer(plot_status.port)
        except Exception:
            pass
        try:
            plot_status.port.reset_output_buffer()
        except Exception:
            pass


def _grbl_handshake_with_retries(port, timeout_s=2.0, attempts=3):
    """Retry handshake for controllers that are booting/busy after reset."""
    all_lines = []
    for attempt_index in range(max(1, int(attempts))):
        ok, lines = _grbl_handshake(port, timeout_s=timeout_s + (0.6 * attempt_index))
        if lines:
            all_lines.extend(lines)
        if ok:
            return True, all_lines

        # Recovery path: clear alarm/hold and request status before next try.
        try:
            port.write(b"\x18")  # Ctrl-X soft reset
            port.flush()
            time.sleep(0.15)
            _read_available_lines(port)
            port.write(b"$X\n")
            port.flush()
            time.sleep(0.10)
            _read_available_lines(port)
            port.write(b"?")
            port.flush()
            time.sleep(0.08)
            _read_available_lines(port)
        except Exception:
            pass
        time.sleep(0.12 + (0.08 * attempt_index))
    return False, all_lines


def _grbl_status_probe(port, timeout_s=1.0):
    """Return True if realtime status returns a '<...>' frame."""
    try:
        port.write(b"?")
        port.flush()
        end = time.time() + timeout_s
        while time.time() < end:
            for line in _read_available_lines(port):
                if line.startswith("<") and line.endswith(">"):
                    return True
            time.sleep(0.02)
    except Exception:
        return False
    return False


def _grbl_settings_probe(port, timeout_s=2.0):
    """Return (ok, lines) when '$$' responds with setting rows like '$130=...'."""
    lines = []
    setting_re = re.compile(r"^\$(\d+)\s*=\s*([^\s]+)")
    try:
        port.write(b"$$\n")
        port.flush()
        end = time.time() + timeout_s
        saw_setting = False
        while time.time() < end:
            new_lines = _read_available_lines(port)
            if new_lines:
                lines.extend(new_lines)
            for line in new_lines:
                low = line.lower()
                if setting_re.match(line.strip()):
                    saw_setting = True
                if low.startswith("error") or low.startswith("alarm"):
                    return False, lines
                if low.startswith("ok") and saw_setting:
                    return True, lines
            time.sleep(0.02)
    except Exception:
        return False, lines
    return False, lines


def _contains_grbl_identity(lines):
    joined = "\n".join(lines).lower()
    return ("grbl" in joined) or ("fluidnc" in joined)


def _extract_grbl_version(lines):
    version_re = re.compile(r"(grbl[^\r\n]*)", re.IGNORECASE)
    for line in lines:
        matched = version_re.search(line)
        if matched:
            return matched.group(1).strip()
    for line in lines:
        if line.startswith("[VER:") or line.startswith("[OPT:"):
            return line.strip()
    return "Grbl"


def query_voltage(options, params, plot_status, warnings):
    """ Check that power supply is detected. """
    if is_grbl(plot_status):
        return
    if params.skip_voltage_check:
        return
    if plot_status.port is not None and not options.preview:
        voltage_ok = ebb_motion.queryVoltage(plot_status.port, False)
        if not voltage_ok:
            warnings.add_new('voltage')


def exhaust_queue(ad_ref):
    """
    Wait until queued motion commands have finished executing
    Uses the QG query http://evil-mad.github.io/EggBot/ebb.html#QG
    Uses time.sleep to sleep as long as motion commands are still executing.

    Query every 50 ms. Also break on keyboard interrupt (if configured) and
        pause button press.

    Requires EBB firmware version 2.6.2 or newer, returns (without error) otherwise,
        not executing any delay time.
    """

    if is_grbl(ad_ref.plot_status):
        grbl_wait_idle(ad_ref.plot_status, ad_ref.receive_pause_request, timeout_s=30.0)
        return

    if not ad_versions.min_fw_version(ad_ref.plot_status, "2.6.2"):
        return
    if ad_ref.plot_status.port is None:
        return
    while True:
        if ad_ref.receive_pause_request(): # Keyboard interrupt detected!
            break
        status_string = ebb_serial.query(ad_ref.plot_status.port, 'QG\r').strip()
        status = int('0x' + status_string, 16)
        if status & 32: # Pause button pressed
            break
        if status & 15 == 0:  # If no commands are queued or executing,
            break               #   and both motors are idle

        time.sleep(0.050) # Use short intervals for responsiveness


def disconnect_port(plot_status):
    """Close active serial port for whichever backend is in use."""
    if not plot_status.port:
        return
    if is_grbl(plot_status):
        grbl_flush_motion(plot_status, timeout_s=5.0, wait_idle=True)
        try:
            plot_status.port.close()
        except Exception:
            pass
    else:
        try:
            ebb_serial.closePort(plot_status.port)
        except Exception:
            pass
    plot_status.port = None
    if is_grbl(plot_status):
        plot_status.grbl_motion_mode_ready = False
        plot_status.grbl_xy_zeroed = False
        plot_status.grbl_z_zeroed = False


def reset_input_buffer(port):
    """Portable clear of incoming serial data."""
    try:
        port.reset_input_buffer()
    except Exception:
        try:
            port.flushInput()
        except Exception:
            pass


def _read_available_lines(port):
    lines = []
    while True:
        try:
            waiting = port.in_waiting
        except Exception:
            waiting = 0
        if waiting <= 0:
            break
        raw = port.readline()
        if not raw:
            break
        try:
            line = raw.decode("utf-8", errors="replace").strip()
        except Exception:
            line = str(raw).strip()
        if line:
            lines.append(line)
    return lines


def _grbl_pending_bytes(plot_status):
    """Return tracked bytes still occupying the Grbl serial RX buffer."""
    return sum(getattr(plot_status, "grbl_pending_lengths", []))


def _grbl_track_line(plot_status, line):
    """Update cached Grbl stream state from one received line."""
    if not line:
        return
    if line.startswith("<") and line.endswith(">"):
        plot_status.grbl_status = line
        plot_status.grbl_last_status_timestamp = time.time()
        _update_grbl_position_cache(plot_status, line)
        return

    low = line.lower()
    if low.startswith("ok"):
        pending = getattr(plot_status, "grbl_pending_lengths", None)
        if pending:
            pending.pop(0)
        return
    if low.startswith("error") or low.startswith("alarm"):
        pending = getattr(plot_status, "grbl_pending_lengths", None)
        if pending:
            pending.pop(0)
        plot_status.grbl_stream_error = line


def _grbl_drain_incoming(plot_status):
    """Drain already-available Grbl responses and update tracking state."""
    if plot_status.port is None:
        return []
    lines = _read_available_lines(plot_status.port)
    for line in lines:
        _grbl_track_line(plot_status, line)
    return lines


def _grbl_write_payload(plot_status, payload, flush=False):
    """Write raw bytes to the serial port; optionally wait for driver flush."""
    plot_status.port.write(payload)
    if flush:
        plot_status.port.flush()


def grbl_send_realtime(plot_status, payload, flush=True):
    """Send a realtime control byte sequence such as jog cancel or feed hold."""
    if plot_status.port is None:
        return False
    try:
        if isinstance(payload, int):
            payload = bytes([payload])
        _grbl_write_payload(plot_status, payload, flush=flush)
        return True
    except Exception:
        return False


def grbl_cancel_jog(plot_status, timeout_s=2.0):
    """Cancel the current jog operation and wait briefly for Grbl to leave Jog state."""
    if not grbl_send_realtime(plot_status, b"\x85", flush=True):
        return False
    end = time.time() + max(timeout_s, 0.2)
    while time.time() < end:
        status_line = grbl_query_status(plot_status, timeout_s=0.25)
        state = grbl_status_state(status_line)
        if state not in ("Jog", "Run"):
            return True
        time.sleep(0.05)
    return True


def grbl_feed_hold(plot_status):
    """Request a feed hold using Grbl realtime '!'."""
    return grbl_send_realtime(plot_status, b"!", flush=True)


def _grbl_wait_for_buffer_space(plot_status, payload_len, timeout_s=3.0):
    """Wait until tracked Grbl RX usage leaves room for one more line."""
    reserve = 4
    buffer_size = max(int(getattr(plot_status, "grbl_rx_buffer_size", 128)), 32)
    end = time.time() + timeout_s
    while time.time() < end:
        _grbl_drain_incoming(plot_status)
        if getattr(plot_status, "grbl_stream_error", None):
            return False
        if (_grbl_pending_bytes(plot_status) + payload_len) <= (buffer_size - reserve):
            return True
        time.sleep(0.002)
    return False


def grbl_flush_motion(plot_status, timeout_s=30.0, wait_idle=False):
    """Wait until queued streamed motion is acknowledged, optionally until machine reports Idle."""
    end = time.time() + timeout_s
    while time.time() < end:
        _grbl_drain_incoming(plot_status)
        if getattr(plot_status, "grbl_stream_error", None):
            return False
        if not getattr(plot_status, "grbl_pending_lengths", []):
            break
        time.sleep(0.003)
    else:
        return False

    if wait_idle:
        return grbl_wait_idle(plot_status, timeout_s=timeout_s)
    return True


def grbl_queue_motion(plot_status, command, timeout_s=3.0):
    """Queue one motion line into the Grbl RX buffer without waiting for its immediate ok."""
    if plot_status.port is None:
        return False, ["no_port"]
    if getattr(plot_status, "grbl_stream_error", None):
        return False, [plot_status.grbl_stream_error]

    payload = command.strip().encode("utf-8") + b"\n"
    if not _grbl_wait_for_buffer_space(plot_status, len(payload), timeout_s=timeout_s):
        _grbl_drain_incoming(plot_status)
        error_line = getattr(plot_status, "grbl_stream_error", None)
        return False, [error_line or "stream_timeout"]

    try:
        _grbl_write_payload(plot_status, payload, flush=False)
    except Exception:
        return False, ["stream_exception"]

    plot_status.grbl_pending_lengths.append(len(payload))
    _grbl_drain_incoming(plot_status)
    if getattr(plot_status, "grbl_stream_error", None):
        return False, [plot_status.grbl_stream_error]
    return True, []


def grbl_send_result(plot_status, command, expect_ok=True, timeout_s=2.0):
    """
    Send one Grbl command and return a structured result dict:
    {"ok": bool, "kind": str, "lines": list[str]}.
    """
    if plot_status.port is None:
        return {"ok": False, "kind": "no_port", "lines": []}
    payload = command.strip().encode("utf-8") + b"\n"
    lines = []
    try:
        if expect_ok and getattr(plot_status, "grbl_pending_lengths", []):
            if not grbl_flush_motion(plot_status, timeout_s=max(timeout_s, 3.0), wait_idle=False):
                error_line = getattr(plot_status, "grbl_stream_error", None)
                if error_line:
                    return {"ok": False, "kind": "stream_error", "lines": [error_line]}
                return {"ok": False, "kind": "stream_timeout", "lines": []}
        _grbl_write_payload(plot_status, payload, flush=expect_ok)
        if not expect_ok:
            return {"ok": True, "kind": "sent", "lines": lines}
        end = time.time() + timeout_s
        while time.time() < end:
            new_lines = _read_available_lines(plot_status.port)
            if new_lines:
                lines.extend(new_lines)
            for line in new_lines:
                _grbl_track_line(plot_status, line)
            for line in new_lines:
                low = line.lower()
                if low.startswith("ok"):
                    return {"ok": True, "kind": "ok", "lines": lines}
                if low.startswith("error"):
                    return {"ok": False, "kind": "error", "lines": lines}
                if low.startswith("alarm"):
                    return {"ok": False, "kind": "alarm", "lines": lines}
            time.sleep(0.01)
    except Exception:
        return {"ok": False, "kind": "exception", "lines": lines}
    return {"ok": False, "kind": "timeout", "lines": lines}


def grbl_send(plot_status, command, expect_ok=True, timeout_s=2.0):
    """Send one Grbl command; return legacy tuple (ok, response_lines)."""
    result = grbl_send_result(
        plot_status,
        command,
        expect_ok=expect_ok,
        timeout_s=timeout_s)
    return result["ok"], result["lines"]


def grbl_initialize_motion(plot_status, timeout_s=2.0, zero_z=True, zero_xy=False):
    """Initialize controller motion mode for plotting/pen moves."""
    need_motion_mode = not bool(getattr(plot_status, "grbl_motion_mode_ready", False))
    need_zero_xy = bool(zero_xy and not getattr(plot_status, "grbl_xy_zeroed", False))
    need_zero_z = bool(zero_z and not getattr(plot_status, "grbl_z_zeroed", False))
    if not (need_motion_mode or need_zero_xy or need_zero_z):
        return True, None, None

    grbl_reset_stream_state(plot_status, clear_io=False)
    _grbl_drain_incoming(plot_status)
    status_line = grbl_query_status(plot_status, timeout_s=min(timeout_s, 0.8))
    state = grbl_status_state(status_line)
    if state and not state.startswith("Idle"):
        grbl_wait_idle(plot_status, timeout_s=max(2.0, timeout_s * 2.0))
    if state and (state.startswith("Hold") or state.startswith("Alarm")):
        unlock_result = grbl_send_result(
            plot_status,
            "$X",
            expect_ok=True,
            timeout_s=timeout_s)
        if not unlock_result["ok"]:
            return False, "$X", unlock_result
        if not grbl_wait_idle(plot_status, timeout_s=max(1.0, timeout_s)):
            return False, "wait_idle", {"ok": False, "kind": "busy", "lines": [state]}

    commands = []
    if need_motion_mode:
        commands.extend(["G90", "G21"])
    if need_zero_xy:
        commands.append("G92 X0 Y0")
    if need_zero_z:
        commands.append("G92 Z0")
    for command in commands:
        result = grbl_send_result(
            plot_status,
            command,
            expect_ok=True,
            timeout_s=max(4.0, timeout_s * 3.0))
        if not result["ok"]:
            return False, command, result
        if command == "G90" or command == "G21":
            plot_status.grbl_motion_mode_ready = True
        elif command == "G92 X0 Y0":
            plot_status.grbl_xy_zeroed = True
        elif command == "G92 Z0":
            plot_status.grbl_z_zeroed = True
    grbl_query_status(plot_status, timeout_s=min(timeout_s, 0.8))
    return True, None, None


def grbl_get_identity(plot_status, timeout_s=1.5):
    """Query '$I' and return informative identity/version lines."""
    if getattr(plot_status, "grbl_is_bluetooth", False):
        timeout_s = max(timeout_s, 3.0)
    cached = getattr(plot_status, "fw_version", "")
    for _attempt in range(2):
        grbl_reset_stream_state(plot_status, clear_io=True)
        _grbl_drain_incoming(plot_status)
        result = grbl_send_result(plot_status, "$I", expect_ok=True, timeout_s=timeout_s)
        if not result["ok"]:
            continue
        lines = []
        for line in result["lines"]:
            stripped = line.strip()
            low = stripped.lower()
            if not stripped or low == "ok":
                continue
            if stripped.startswith("<") and stripped.endswith(">"):
                continue
            lines.append(stripped)
        if lines:
            return lines
        time.sleep(0.12)
    cached = getattr(plot_status, "fw_version", "")
    return [cached] if cached else []


def grbl_query_status(plot_status, timeout_s=0.5):
    """Poll realtime status ('?') and return the last '<...>' line."""
    if plot_status.port is None:
        return None
    try:
        plot_status.port.write(b"?")
        plot_status.port.flush()
        end = time.time() + timeout_s
        last = None
        while time.time() < end:
            for line in _read_available_lines(plot_status.port):
                _grbl_track_line(plot_status, line)
                if line.startswith("<") and line.endswith(">"):
                    last = line
            if last:
                return last
            time.sleep(0.01)
    except Exception:
        return None
    return None


def grbl_status_state(status_line):
    """Return status state token from '<State|...>' frame, else None."""
    if not status_line or not status_line.startswith("<"):
        return None
    body = status_line[1:]
    token = body.split("|", 1)[0]
    token = token.split(">", 1)[0].strip()
    if not token:
        return None
    return token


def grbl_get_positions(plot_status, timeout_s=0.8):
    """
    Query and return both MPos/WPos in logical and physical inches.
    """
    status_line = grbl_query_status(plot_status, timeout_s=timeout_s)
    if not status_line:
        return {"mpos": getattr(plot_status, "grbl_mpos_in", None),
                "wpos": getattr(plot_status, "grbl_wpos_in", None),
                "mpos_phys": getattr(plot_status, "grbl_mpos_phys_in", None),
                "wpos_phys": getattr(plot_status, "grbl_wpos_phys_in", None),
                "state": None}
    return {"mpos": getattr(plot_status, "grbl_mpos_in", None),
            "wpos": getattr(plot_status, "grbl_wpos_in", None),
            "mpos_phys": getattr(plot_status, "grbl_mpos_phys_in", None),
            "wpos_phys": getattr(plot_status, "grbl_wpos_phys_in", None),
            "state": grbl_status_state(status_line)}


def grbl_wait_idle(plot_status, break_requested=None, timeout_s=30.0):
    """Wait until machine reports Idle."""
    end = time.time() + timeout_s
    while time.time() < end:
        if break_requested and break_requested():
            return False
        status_line = grbl_query_status(plot_status, timeout_s=0.4)
        if status_line and status_line.startswith("<Idle"):
            return True
        time.sleep(0.05)
    return False


def _extract_axis_from_status(status_line, axis_name):
    """Extract one axis value from MPos/WPos payload."""
    token = f"{axis_name}Pos:"
    idx = status_line.find(token)
    if idx < 0:
        return None
    payload = status_line[idx + len(token):]
    values = payload.split("|", 1)[0].split(",")
    if len(values) < 2:
        return None
    try:
        return float(values[0]), float(values[1])
    except ValueError:
        return None


def _update_grbl_position_cache(plot_status, status_line):
    """Cache last known machine/work positions from a realtime status frame."""
    mpos = _extract_axis_from_status(status_line, "M")
    wpos = _extract_axis_from_status(status_line, "W")
    if mpos is not None:
        plot_status.grbl_mpos_phys_in = (
            mpos[0] / 25.4,
            mpos[1] / 25.4)
        plot_status.grbl_mpos_in = _axis_map_in(
            plot_status,
            mpos[0] / 25.4,
            mpos[1] / 25.4)
    if wpos is not None:
        plot_status.grbl_wpos_phys_in = (
            wpos[0] / 25.4,
            wpos[1] / 25.4)
        plot_status.grbl_wpos_in = _axis_map_in(
            plot_status,
            wpos[0] / 25.4,
            wpos[1] / 25.4)


def grbl_get_position(plot_status, timeout_s=0.8, prefer_machine=True):
    """Query status and return XY in inches from MPos/WPos."""
    status_line = grbl_query_status(plot_status, timeout_s=timeout_s)
    if not status_line:
        return None
    if prefer_machine and getattr(plot_status, "grbl_mpos_in", None):
        return plot_status.grbl_mpos_in
    if getattr(plot_status, "grbl_wpos_in", None):
        return plot_status.grbl_wpos_in
    return getattr(plot_status, "grbl_mpos_in", None)


def read_grbl_settings(plot_status, timeout_s=2.0):
    """Read and parse '$$' settings into a dict keyed by integer setting id."""
    parsed = {}
    ok, lines = grbl_send(plot_status, "$$", expect_ok=True, timeout_s=timeout_s)
    if not ok:
        return parsed
    setting_re = re.compile(r"^\$(\d+)\s*=\s*([^\s]+)")
    for line in lines:
        matched = setting_re.match(line.strip())
        if not matched:
            continue
        key = int(matched.group(1))
        try:
            value = float(matched.group(2))
        except ValueError:
            value = matched.group(2)
        parsed[key] = value
    return parsed


def apply_grbl_settings_to_params(plot_status, params):
    """
    Map common Grbl settings into travel/speed defaults used by AxiDraw logic.
    This keeps plotting limits synchronized with firmware configuration.
    """
    settings = getattr(plot_status, "grbl_settings", None) or {}
    x_max = settings.get(130)
    y_max = settings.get(131)
    if isinstance(x_max, (int, float)) and x_max > 0:
        params.x_travel_default = x_max / 25.4
    if isinstance(y_max, (int, float)) and y_max > 0:
        params.y_travel_default = y_max / 25.4
    x_rate = settings.get(110)
    y_rate = settings.get(111)
    x_accel = settings.get(120)
    y_accel = settings.get(121)
    speed_limit = _derive_speed_limit(x_rate, y_rate)
    accel_limit = _derive_accel_limit(x_accel, y_accel)
    if speed_limit is not None:
        params.speed_lim_xy_hr = speed_limit
        params.speed_lim_xy_lr = speed_limit
    if accel_limit is not None:
        params.accel_rate = accel_limit


def _derive_speed_limit(x_rate, y_rate):
    """Map Grbl max feed from mm/min to in/s style AxiDraw speed limit."""
    rates = [value for value in (x_rate, y_rate) if isinstance(value, (int, float)) and value > 0]
    if not rates:
        return None
    mm_per_s = min(rates) / 60.0
    return max(mm_per_s / 25.4 * 110.0, 1.0)


def _derive_accel_limit(x_accel, y_accel):
    """Map Grbl accel from mm/s^2 to AxiDraw accel factor."""
    values = [value for value in (x_accel, y_accel) if isinstance(value, (int, float)) and value > 0]
    if not values:
        return None
    min_in_s2 = min(values) / 25.4
    return max(min(min_in_s2 * 5.0, 500.0), 1.0)


def grbl_settings_conflicts(options, params, plot_status):
    """Return human-readable warnings when user config disagrees with firmware settings."""
    conflict_messages = []
    settings = getattr(plot_status, "grbl_settings", None) or {}
    x_max = settings.get(130)
    y_max = settings.get(131)
    if isinstance(x_max, (int, float)):
        x_in = x_max / 25.4
        if abs(x_in - params.x_travel_default) > 0.05:
            conflict_messages.append(
                gettext.gettext(
                    "Firmware X travel differs from configured value; using firmware value."))
    if isinstance(y_max, (int, float)):
        y_in = y_max / 25.4
        if abs(y_in - params.y_travel_default) > 0.05:
            conflict_messages.append(
                gettext.gettext(
                    "Firmware Y travel differs from configured value; using firmware value."))
    return conflict_messages


def list_grbl_ports():
    """Enumerate serial devices to be used as Grbl candidates."""
    return [item.device for item in _grbl_port_records()]


def list_grbl_port_info():
    """Enumerate serial devices with descriptions for UI/reporting."""
    return [
        {
            "device": item.device,
            "description": getattr(item, "description", "") or "",
            "hwid": getattr(item, "hwid", "") or "",
        }
        for item in _grbl_port_records()
    ]


def grbl_move_linear(plot_status, x_in, y_in, feed_in_s, rapid=False, timeout_s=3.0):
    """Absolute XY move in inches."""
    x_in, y_in = _axis_map_out(plot_status, x_in, y_in)
    x_mm = x_in * 25.4
    y_mm = y_in * 25.4
    if rapid:
        cmd = f"G0 X{x_mm:.3f} Y{y_mm:.3f}"
    else:
        feed_mm_min = max(feed_in_s * 25.4 * 60.0, 1.0)
        cmd = f"G1 X{x_mm:.3f} Y{y_mm:.3f} F{feed_mm_min:.2f}"
    return grbl_send(plot_status, cmd, expect_ok=True, timeout_s=timeout_s)


def grbl_move_machine_linear(plot_status, x_in, y_in, rapid=True, timeout_s=3.0):
    """Absolute XY move in machine coordinates (G53), using physical inches as input."""
    x_mm = x_in * 25.4
    y_mm = y_in * 25.4
    cmd = f"G53 G0 X{x_mm:.3f} Y{y_mm:.3f}" if rapid else f"G53 G1 X{x_mm:.3f} Y{y_mm:.3f}"
    return grbl_send(plot_status, cmd, expect_ok=True, timeout_s=timeout_s)


def grbl_queue_linear(plot_status, x_in, y_in, feed_in_s, rapid=False, timeout_s=3.0):
    """Queue one absolute XY move in inches into the Grbl motion stream."""
    x_in, y_in = _axis_map_out(plot_status, x_in, y_in)
    x_mm = x_in * 25.4
    y_mm = y_in * 25.4
    if rapid:
        cmd = f"G0 X{x_mm:.3f} Y{y_mm:.3f}"
    else:
        feed_mm_min = max(feed_in_s * 25.4 * 60.0, 1.0)
        cmd = f"G1 X{x_mm:.3f} Y{y_mm:.3f} F{feed_mm_min:.2f}"
    return grbl_queue_motion(plot_status, cmd, timeout_s=timeout_s)


def grbl_jog(plot_status, dx_in, dy_in, feed_in_s, timeout_s=2.0):
    """Relative jog using Grbl real-time jogging command when available."""
    dx_out, dy_out = _axis_map_out(plot_status, dx_in, dy_in)
    dx_mm = dx_out * 25.4
    dy_mm = dy_out * 25.4
    feed_mm_min = max(feed_in_s * 25.4 * 60.0, 1.0)
    cmd = f"$J=G21G91 X{dx_mm:.3f} Y{dy_mm:.3f} F{feed_mm_min:.2f}"
    return grbl_send(plot_status, cmd, expect_ok=True, timeout_s=timeout_s)


def _axis_map_out(plot_status, x_in, y_in):
    """Map logical XY into physical XY for outgoing Grbl motion."""
    x_out = x_in
    y_out = y_in
    origin = _normalize_coordinate_origin(getattr(plot_status, "grbl_coordinate_origin", "bottom_left"))
    if origin == "top_left":
        y_out = -y_out
    elif origin == "top_right":
        x_out = -x_out
        y_out = -y_out
    elif origin == "bottom_right":
        x_out = -x_out
    elif origin == "center":
        y_out = -y_out
    if getattr(plot_status, "grbl_axis_invert_x", False):
        x_out = -x_out
    if getattr(plot_status, "grbl_axis_invert_y", False):
        y_out = -y_out
    if getattr(plot_status, "grbl_axis_swap_xy", False):
        x_out, y_out = y_out, x_out
    return x_out, y_out


def _axis_map_in(plot_status, x_in, y_in):
    """Map physical XY back into logical XY from Grbl status."""
    x_out = x_in
    y_out = y_in
    if getattr(plot_status, "grbl_axis_swap_xy", False):
        x_out, y_out = y_out, x_out
    if getattr(plot_status, "grbl_axis_invert_x", False):
        x_out = -x_out
    if getattr(plot_status, "grbl_axis_invert_y", False):
        y_out = -y_out
    origin = _normalize_coordinate_origin(getattr(plot_status, "grbl_coordinate_origin", "bottom_left"))
    if origin == "top_left":
        y_out = -y_out
    elif origin == "top_right":
        x_out = -x_out
        y_out = -y_out
    elif origin == "bottom_right":
        x_out = -x_out
    elif origin == "center":
        y_out = -y_out
    return x_out, y_out


def _normalize_coordinate_origin(value):
    """Normalize origin aliases to canonical names used by the plotter mapping."""
    raw = str(value or "").strip().lower()
    alias_map = {
        "left_upper": "top_left",
        "leftup": "top_left",
        "left_up": "top_left",
        "origin": "top_left",
        "right_upper": "top_right",
        "rightup": "top_right",
        "right_up": "top_right",
        "left_lower": "bottom_left",
        "leftdown": "bottom_left",
        "left_down": "bottom_left",
        "right_lower": "bottom_right",
        "rightdown": "bottom_right",
        "right_down": "bottom_right",
        "centre": "center",
    }
    raw = alias_map.get(raw, raw)
    if raw not in {"top_left", "top_right", "bottom_left", "bottom_right", "center"}:
        return "bottom_left"
    return raw


def grbl_write_setting(plot_status, setting_id, value, timeout_s=2.0):
    """Write one Grbl setting like '$3=5' and return True on success."""
    command = f"${int(setting_id)}={value}"
    ok, _lines = grbl_send(plot_status, command, expect_ok=True, timeout_s=timeout_s)
    return ok
