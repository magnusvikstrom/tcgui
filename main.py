import subprocess
import os
import re
import argparse
import json
import tempfile
import time
import copy

from flask import Flask, render_template, request, url_for, flash, abort


BANDWIDTH_UNITS = [
    "bps",  # Bits per second
    "kbps",  # Kilobits per second
    "mbps",  # Megabits per second
    "gbps",  # Gigabits per second
    "tbps",  # Terabits per second
]

STANDARD_UNIT = "mbps"
SETTINGS_CACHE_TTL_SECONDS = float(os.environ.get("TCGUI_SETTINGS_CACHE_TTL", "30"))


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev')

pattern = None
dev_list = None
settings_cache = {"data": None, "timestamp": 0.0}

app.static_folder = "static"


def parse_filter_rule_key(rule_key):
    filter_info = {}

    for part in rule_key.split(","):
        item = part.strip()
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        filter_info[key.strip()] = value.strip()

    return filter_info


def normalize_tc_id_suffix(value, separator):
    if not value or separator not in value:
        return None

    suffix = value.split(separator, 1)[1].strip().lower()
    if suffix == "":
        return None

    try:
        return format(int(suffix, 16), "x")
    except ValueError:
        return suffix.lstrip("0") or "0"


def build_filter_metadata(settings_by_direction):
    filters_by_minor = {}

    for rule_key, rule_options in settings_by_direction.items():
        minor_id = normalize_tc_id_suffix(rule_options.get("filter_id"), "::")
        if not minor_id:
            continue

        filter_info = parse_filter_rule_key(rule_key)
        filter_info["filter_id"] = rule_options.get("filter_id")
        filters_by_minor[minor_id] = filter_info

    return filters_by_minor


def get_filter_flowid_map(dev):
    flow_ids_by_filter_minor = {}

    try:
        out = subprocess.check_output(
            ["tc", "filter", "show", "dev", dev],
            stderr=subprocess.STDOUT
        ).decode()
    except Exception as e:
        print("Stats filter mapping error:", e)
        return flow_ids_by_filter_minor

    current_filter_minor = None

    for line in out.splitlines():
        filter_match = re.search(r"\bfh ([0-9a-fA-F:]+)\b", line)
        if filter_match:
            current_filter_minor = normalize_tc_id_suffix(filter_match.group(1), "::")

        flowid_match = re.search(r"\bflowid ([0-9a-fA-F:]+)\b", line)
        if flowid_match and current_filter_minor:
            flow_minor = normalize_tc_id_suffix(flowid_match.group(1), ":")
            if flow_minor:
                flow_ids_by_filter_minor[current_filter_minor] = flow_minor

    return flow_ids_by_filter_minor


def attach_filter_metadata(qdiscs, settings_by_direction, flow_ids_by_filter_minor):
    filters_by_minor = build_filter_metadata(settings_by_direction)
    filters_by_flow_minor = {}

    for filter_minor, filter_info in filters_by_minor.items():
        flow_minor = flow_ids_by_filter_minor.get(filter_minor)
        if flow_minor:
            filters_by_flow_minor[flow_minor] = filter_info

    for qdisc in qdiscs:
        minor_id = normalize_tc_id_suffix(qdisc.get("parent"), ":")
        if not minor_id:
            continue

        filter_info = filters_by_flow_minor.get(minor_id) or filters_by_minor.get(minor_id)
        if filter_info:
            qdisc["filter"] = filter_info

    return qdiscs


def load_settings_from_tcshow():
    settings = {}

    for dev in dev_list.split(" "):
      command = ["tcshow", dev]
      proc = subprocess.run(
          command,
          stdout=subprocess.PIPE,
          stderr=subprocess.PIPE,
          text=True,
      )
      output = proc.stdout.strip()
      stderr_output = proc.stderr.strip()

      if proc.returncode != 0:
          print(
              "tcshow failed for %s (rc=%s): %s"
              % (dev, proc.returncode, stderr_output or output or "<no output>")
          )
          settings[dev] = {}
          continue

      if not output:
          print("tcshow returned empty output for %s" % dev)
          settings[dev] = {}
          continue

      try:
          parsed_output = json.loads(output)
      except json.JSONDecodeError as e:
          print("tcshow returned invalid JSON for %s: %s" % (dev, output))
          print(e)
          settings[dev] = {}
          continue

      if dev not in parsed_output:
          print(
              "tcshow output for %s missing device key. Keys: %s"
              % (dev, list(parsed_output.keys()))
          )
          settings[dev] = {}
          continue

      settings[dev] = parsed_output[dev]

    print("Settings: %s " % settings)
    return settings


def refresh_settings_cache():
    settings = load_settings_from_tcshow()
    settings_cache["data"] = settings
    settings_cache["timestamp"] = time.time()
    return copy.deepcopy(settings)


def parse_arguments():
    parser = argparse.ArgumentParser(description="TC web GUI")
    parser.add_argument(
        "--ip", type=str, required=False, help="The IP where the server is listening"
    )
    parser.add_argument(
        "--port",
        type=int,
        required=False,
        help="The port where the server is listening",
    )
    parser.add_argument(
        "--dev",
        type=str,
        nargs="*",
        required=False,
        help="The interfaces to restrict to",
    )
    parser.add_argument(
        "--regex", type=str, required=False, help="A regex to match interfaces"
    )
    parser.add_argument("--debug", action="store_true", help="Run Flask in debug mode")
    return parser.parse_args()


# The impairment console (asmira-vs-quic, impair-console/) can shape the
# same interface. It adds a clsact qdisc while it runs a session and removes
# it afterwards; tcconfig never creates one. While it is there, an incoming
# rule cannot be added (the ingress hook is taken) although tcset exits 0,
# and an outgoing rule stacks on the console's shaping of its clients, so
# changes are refused. Clear All stays allowed: tcdel leaves the clsact qdisc
# and its filters alone. The other way round, the console refuses to start a
# run while an ingress qdisc (tcgui's incoming rules) is on the interface.
# A run with the console's terminal PEP is shaped on lo instead, with its
# clients' traffic redirected to a local proxy, so it is recognised by the
# console's lock on the interface plus a clsact qdisc on lo.
def qdisc_kinds(dev):
    try:
        out = subprocess.run(
            ["tc", "-j", "qdisc", "show", "dev", dev],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        ).stdout
        return {q.get("kind") for q in json.loads(out or "[]")}
    except (subprocess.CalledProcessError, ValueError) as e:
        print("tc qdisc show failed for %s: %s" % (dev, e))
        return set()


def console_running(dev):
    """Whether an impairment console holds its lock on DEV: an abstract Unix
    socket named impair-console-DEV, scoped to this network namespace."""
    try:
        with open("/proc/net/unix") as f:
            return any(line.split()[-1] == "@impair-console-" + dev for line in f if len(line.split()) > 7)
    except OSError:
        return False


def console_run(dev):
    """How an impairment console run shapes DEV's traffic now: "clsact" (a
    qdisc on DEV), "pep" (a PEP run, shaped on lo), or None."""
    if "clsact" in qdisc_kinds(dev):
        return "clsact"
    if dev != "lo" and console_running(dev) and "clsact" in qdisc_kinds("lo"):
        return "pep"
    return None


def console_conflict(devs):
    """Why tcset may not change DEVS now, or None."""
    runs = {dev: console_run(dev) for dev in devs}
    busy = [dev for dev, run in runs.items() if run == "clsact"]
    pep = [dev for dev, run in runs.items() if run == "pep"]
    if busy:
        return (
            "Not applied: %s has a clsact qdisc, most likely an impairment console run in progress. "
            "An incoming rule cannot be added while it is there, and an outgoing rule would stack on "
            "that run's shaping. Try again when the run has ended." % ", ".join(busy)
        )
    if pep:
        return (
            "Not applied: an impairment console run with a terminal PEP is in progress on %s "
            "(shaped on lo). A rule here would stack on that run's shaping, and incoming rules "
            "would stop the console from starting further runs. Try again when the run has ended."
            % ", ".join(pep)
        )
    return None


def interface_notices():
    notices = []
    for dev in dev_list.split(" "):
        kinds = qdisc_kinds(dev)
        run = console_run(dev)
        if run == "clsact":
            notices.append(
                "%s: a clsact qdisc is present, most likely an impairment console run; "
                "changes to %s are refused until it is gone. Clear All is still safe." % (dev, dev)
            )
        elif run == "pep":
            notices.append(
                "%s: an impairment console run with a terminal PEP is in progress (shaped on lo); "
                "changes to %s are refused until it ends. Clear All is still safe." % (dev, dev)
            )
        if run != "clsact" and "ingress" in kinds and console_running(dev):
            notices.append(
                "%s: the impairment console on %s cannot start runs while incoming rules are set here. "
                "Clear All removes them." % (dev, dev)
            )
    return notices


def run_tcset(command):
    """Run tcset; (ok, output). tcset exits 0 even when a tc command it issues
    fails, and logs [ERROR] instead, so both are checked."""
    proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    output = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout)
    print(output)
    return proc.returncode == 0 and "[ERROR]" not in output, output


def refuse(message, status):
    print(message)
    return message, status, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/")
def main():
    settings = get_settings(force_refresh=True)

    return render_template(
        "main.html", units=BANDWIDTH_UNITS, standard_unit=STANDARD_UNIT, settings=settings, interfaces=dev_list.split(" "),
        notices=interface_notices(),
    )


@app.route("/import_settings", methods=["POST"])
def import_settings():
    try:
        settings = request.form["Settings"]
        settings = json.loads(settings)
    except ValueError as e:
        abort(400, "Error")

    if not isinstance(settings, dict):
        abort(400, "Error")
    conflict = console_conflict([dev for dev in dev_list.split(" ") if dev in settings])
    if conflict:
        return refuse(conflict, 409)

    delete_all()
    f = tempfile.NamedTemporaryFile(delete = False, mode = "w")
    f.write(json.dumps(settings, indent=4))
    f.close()
    command = "tcset --import-setting %s" % f.name
    ok, output = run_tcset(command.split(" "))
    os.unlink(f.name)
    if not ok:
        return refuse("tcset failed:\n" + output[-1000:], 400)
    flash("Successfully updated settings")


    return get_settings(force_refresh=True)

@app.route("/remove_all", methods=["POST"])
def remove_all():
    delete_all()

    flash("Successfully cleared settings")

    # Wait a while before getting settings to avoid empty json response
    time.sleep(0.5)

    return get_settings(force_refresh=True)

@app.route("/add_rule", methods=["POST"])
def add_rule():
    interface = request.form["Interface"]
    direction = request.form["Direction"]
    network = request.form["Network"]
    network_type = request.form["NetworkType"]
    delay = request.form["Delay"]
    delay_variance = request.form["DelayVariance"]
    loss = request.form["Loss"]
    duplicate = request.form["Duplicate"]
    reorder = request.form["Reorder"]
    corrupt = request.form["Corrupt"]
    rate = request.form["Rate"]
    rate_unit = request.form["rate_unit"]
    limit = request.form["Limit"]

    # apply new setup
    command = "tcset --change %s" % (interface)
    if direction != "":
        command += " --direction %s" % direction
    if network != "":
        if network_type == "source":
            command += " --src-network %s" % network
        else:
            command += " --dst-network %s" % network
    if rate != "":
        command += " --rate %s%s" % (rate, rate_unit)
    if delay != "":
        command += " --delay %sms" % delay
        if delay_variance != "":
            command += " --delay-distro %sms" % delay_variance
    if loss != "":
        command += " --loss %s" % loss
    if duplicate != "":
        command += " --duplicate %s" % duplicate
    if reorder != "":
        command += " --reordering %s" % reorder
    if corrupt != "":
        command += " --corrupt %s" % corrupt
    if limit != "":
        command += " --limit %s" % limit
    print(command)

    conflict = console_conflict([interface])
    if conflict:
        return refuse(conflict, 409)
    ok, output = run_tcset(command.split(" "))
    if not ok:
        return refuse("tcset failed:\n" + output[-1000:], 400)
    flash("Successfully updated settings")

    return get_settings(force_refresh=True)

def detect_ifb(dev):
    try:
        out = subprocess.check_output(
            ["tc", "filter", "show", "dev", dev, "ingress"],
            stderr=subprocess.STDOUT
        ).decode()

        # Find mirred redirect target(s)
        matches = re.findall(r"mirred.*?device (\w+)", out)

        if matches:
            return matches[0]  # first IFB device
    except:
        pass

    return None

@app.route("/stats")
def stats():
    result = {}
    settings = get_settings(as_string=False)

    for dev in dev_list.split(" "):
        result[dev] = {}
        device_settings = settings.get(dev, {})
        outgoing_flow_ids = get_filter_flowid_map(dev)
        result[dev]["outgoing_filters"] = device_settings.get("outgoing", {})

        # Outgoing is always on the main device
        try:
            out = subprocess.check_output(
                ["tc", "-j", "-s", "qdisc", "show", "dev", dev],
                stderr=subprocess.STDOUT
            ).decode()
            qdiscs = json.loads(out)
        except Exception as e:
            print("Stats outgoing error:", e)
            qdiscs = []

        # Load outgoing classes (needed for rate)
        class_rate_map = {}
        try:
            out_classes = subprocess.check_output(
                ["tc", "-j", "-s", "class", "show", "dev", dev],
                stderr=subprocess.STDOUT
            ).decode()
            classes = json.loads(out_classes)

            for c in classes:
                h = c.get("handle")
                if h and c.get("rate") is not None:
                    class_rate_map[h] = c["rate"]

        except Exception as e:
            print("Stats outgoing class error:", e)

        # Merge class rate → qdisc
        for q in qdiscs:
            parent = q.get("parent")
            if parent and parent in class_rate_map:
                opts = q.setdefault("options", {})
                opts["rate"] = class_rate_map[parent] * 8

        qdiscs = attach_filter_metadata(
            qdiscs,
            device_settings.get("outgoing", {}),
            outgoing_flow_ids,
        )
        result[dev]["outgoing"] = qdiscs

        # Detect IFB device for incoming shaping
        ifb = detect_ifb(dev)

        if ifb:
            incoming_flow_ids = get_filter_flowid_map(ifb)
            result[dev]["incoming_filters"] = device_settings.get("incoming", {})
            try:
                out = subprocess.check_output(
                    ["tc", "-j", "-s", "qdisc", "show", "dev", ifb],
                    stderr=subprocess.STDOUT
                ).decode()
                qdiscs_in = json.loads(out)
            except Exception as e:
                print("Stats incoming error:", e)
                qdiscs_in = []

            # Load IFB classes (for rate)
            class_rate_map_in = {}
            try:
                out_classes = subprocess.check_output(
                    ["tc", "-j", "-s", "class", "show", "dev", ifb],
                    stderr=subprocess.STDOUT
                ).decode()
                classes_in = json.loads(out_classes)

                for c in classes_in:
                    h = c.get("handle")
                    if h and c.get("rate") is not None:
                        class_rate_map_in[h] = c["rate"]

            except Exception as e:
                print("Stats incoming class error:", e)

            # Merge incoming class rate → qdisc
            for q in qdiscs_in:
                parent = q.get("parent")
                if parent and parent in class_rate_map_in:
                    opts = q.setdefault("options", {})
                    opts["rate"] = class_rate_map_in[parent] * 8

            qdiscs_in = attach_filter_metadata(
                qdiscs_in,
                device_settings.get("incoming", {}),
                incoming_flow_ids,
            )
            result[dev]["incoming"] = qdiscs_in

        else:
            result[dev]["incoming"] = []
            result[dev]["incoming_filters"] = device_settings.get("incoming", {})

    return json.dumps(result)

def get_settings(as_string = True, force_refresh = False):
    cache_data = settings_cache.get("data")
    cache_is_stale = (
        SETTINGS_CACHE_TTL_SECONDS <= 0
        or cache_data is None
        or (time.time() - settings_cache.get("timestamp", 0.0)) > SETTINGS_CACHE_TTL_SECONDS
    )

    if force_refresh or cache_is_stale:
      settings = refresh_settings_cache()
    else:
      settings = copy.deepcopy(cache_data)

    if as_string:
      return json.dumps(settings, indent=4)
    else:
      return settings

def delete_all():
    for dev in dev_list.split(" "):
      command = "tcdel --all %s" % dev
      command = command.split(" ")
      proc = subprocess.Popen(command, stdout=subprocess.PIPE)


if __name__ == "__main__":
    if os.geteuid() != 0:
        print(
            "You need to have root privileges to run this script.\n"
            "Please try again, this time using 'sudo'. Exiting."
        )
        exit(1)

    # TC Variables
    args = parse_arguments()

    pattern = os.environ.get("TCGUI_REGEX")
    if args.regex:
        pattern = re.compile(args.regex)

    dev_list = os.environ.get("TCGUI_DEV")
    if args.dev:
        dev_list = args.dev

    # Flask Variable
    app_args = {}

    app_args["host"] = os.environ.get("TCGUI_IP")
    app_args["port"] = os.environ.get("TCGUI_PORT")

    if args.ip:
        app_args["host"] = args.ip
    if args.port:
        app_args["port"] = args.port
    if not args.debug:
        app_args["debug"] = False
    app.debug = True
    app.tc_cmd = "tc"
    app.run(**app_args)
