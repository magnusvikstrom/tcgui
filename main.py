import subprocess
import os
import re
import argparse
import json
import tempfile
import time

from flask import Flask, render_template, request, url_for, flash, abort


BANDWIDTH_UNITS = [
    "bps",  # Bits per second
    "kbps",  # Kilobits per second
    "mbps",  # Megabits per second
    "gbps",  # Gigabits per second
    "tbps",  # Terabits per second
]

STANDARD_UNIT = "mbps"


app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev')

pattern = None
dev_list = None

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


@app.route("/")
def main():
    settings = get_settings()

    return render_template(
        "main.html", units=BANDWIDTH_UNITS, standard_unit=STANDARD_UNIT, settings=settings, interfaces=dev_list.split(" ")
    )


@app.route("/import_settings", methods=["POST"])
def import_settings():
    try:
        settings = request.form["Settings"]
        settings = json.loads(settings)
    except ValueError as e:
        abort(400, "Error")

    try:
        delete_all()
        f = tempfile.NamedTemporaryFile(delete = False, mode = "w")
        f.write(json.dumps(settings, indent=4))
        f.close()
        command = "tcset --import-setting %s" % f.name
        command = command.split(" ")
        proc = subprocess.check_output(command, stderr=subprocess.STDOUT)
        os.unlink(f.name)
        flash("Successfully updated settings")
    except subprocess.CalledProcessError as e:
        print(e.output)
        abort(400, "Error")


    return get_settings()

@app.route("/remove_all", methods=["POST"])
def remove_all():
    delete_all()

    flash("Successfully cleared settings")

    # Wait a while before getting settings to avoid empty json response
    time.sleep(0.5)

    return get_settings()

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

    try:
        command = command.split(" ")
        proc = subprocess.check_output(command, stderr=subprocess.STDOUT)
        flash("Successfully updated settings")
    except subprocess.CalledProcessError as e:
        print(e.output)
        flash("Invalid settings")

    return get_settings()

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

def get_settings(as_string = True):
    settings = {}

    for dev in dev_list.split(" "):
      command = "tcshow %s" % dev
      command = command.split(" ")
      proc = subprocess.Popen(command, stdout=subprocess.PIPE)
      output = proc.communicate()[0].decode()
      settings[dev] = json.loads(output)[dev]

    print("Settings: %s " % settings)

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
