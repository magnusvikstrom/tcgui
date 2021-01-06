import subprocess
import os
import re
import argparse
import json
import tempfile

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
    print(command)

    try:
        command = command.split(" ")
        proc = subprocess.check_output(command, stderr=subprocess.STDOUT)
        flash("Successfully updated settings")
    except subprocess.CalledProcessError as e:
        print(e.output)
        flash("Invalid settings")

    return get_settings()


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
