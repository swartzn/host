#!/usr/bin/python

# (c) 2021, NetApp, Inc
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)
from __future__ import absolute_import, division, print_function
__metaclass__ = type


DOCUMENTATION = """
---
module: update_conf
short_description: Update configuration file
description:
    - Update an existing configuration file with specified options.
author: Nathan Swartz (@ndswartz)
options:
    path:
        description:
            - Path to configuration file to be updated.
            - Hidden copy of the configuration file will be made and will not be indicated if changes were not needed.
            - The configuration copy is important to keep the original defaults, so if an option is removed at a later time then the default will be known.
        type: str
        required: false
    copy_extension:
        description: String that will be postpended to the I(path) file name.
        type: str
        required: false
        default: ".~original"
    src:
        description: Source for the base standard Linux conf file.
        type: str
        required: false
    dest:
        description: Destination for the updated standard Linux conf file.
        type: str
        required: false
    options:
        description: Dictionary containing the options key-value pairs to update conf file.
        type: dict
        required: false
        default: {}
    pattern:
        description:
            - Regular expression pattern to capture and update options
            - Must return three groups in the format "^(option)(equivalence)(value)$.
        type: str
        required: false
        default: "^([A-Za-z0-9_-]+)( *= *)(.*)$"
    padding:
        description: Ensures there's padding after the equivalence.
        type: bool
        require: false
    comment_start:
        description: String that begins a comment line.
        type: str
        required: false
        default: "#"
    mode:
        description:
            - The permissions must be in octal number form ("0644", "644")
            - When not specified, the file permissions will be determined by the operating system defaults.
        type: str
        required: false
    block_message:
        description: The message on the begin and end marker lines within the configuration file for options that were not found.
        type: str
        required: false
        default: E-SERIES ANSIBLE MANAGED BLOCK
notes:
    - Configuration file with options that are not found will be placed at the end of the file within a comment block and a warning will be issued.
"""

EXAMPLES = """
- name: test update_conf module
  netapp_eseries.host.update_conf:
    path: /root/beegfs-mgmtd.conf
    options: {connMgmtdPortTCP: 8008, connMgmtdPortUDP: 18008}
    mode: "0644"
  become: true

- name: test update_conf module
  netapp_eseries.host.update_conf:
    src: /etc/beegfs/beegfs-mgmtd.conf
    dest: /root/beegfs-mgmtd.conf
    options: {connMgmtdPortTCP: 8008, connMgmtdPortUDP: 18008}
    mode: "0644"
  become: true
"""

from ansible.module_utils._text import to_native
from ansible.module_utils.basic import AnsibleModule
import re
from os.path import exists, isfile, basename, dirname
from os import chmod, stat

class UpdateConfigFile(object):
    def __init__(self):
        ansible_options = dict(
            path=dict(type="str", require=False),
            copy_extension=dict(type="str", required=False, default=".~original"),
            src=dict(type="str", require=False),
            dest=dict(type="str", require=False),
            options=dict(type="dict", required=False, default={}),
            pattern=dict(type="str", required=False, default="^([A-Za-z0-9_-]+)( *= *)(.*)$"),
            padding=dict(type="bool", required=False, default=True),
            comment_start=dict(type="str", required=False, default="#"),
            mode=dict(type="str", required=False),
            block_message=dict(type="str", required=False, default="E-SERIES ANSIBLE MANAGED BLOCK")
        )
        self.module = AnsibleModule(argument_spec=ansible_options,
                                    mutually_exclusive=[["path", "src"]],
                                    required_together=[["src", "dest"]],
                                    supports_check_mode=True)

        args = self.module.params
        if args["path"]:
            self.path = args["path"]
            path_directory = dirname(args["path"])
            path_filename = basename(args["path"])
            self.src = path_directory + "/." + path_filename + args["copy_extension"]
            self.dest = args["path"]
        else:
            self.path = None
            self.src = args["src"]
            self.dest = args["dest"]
        self.options = args["options"]
        self.pattern = args["pattern"]
        self.padding = args["padding"]
        self.comment_start = args["comment_start"]
        self.mode = args["mode"]
        self.block_message = args["block_message"]

        self.copy_lines_cached = None
        self.update_required_cached = None

    @property
    def updated_copy(self):
        """Create a copy of the source conf file in memory and update the options."""
        options = self.options.keys()
        options_applied = []

        if self.copy_lines_cached is None:
            with open(self.src, "r") as fh:
                self.copy_lines_cached = fh.readlines()

            # Update the copy with the options provided.
            for index, line in enumerate(self.copy_lines_cached):
                result = re.search(self.pattern, line)
                if result:
                    option, equivalence, value = list(result.groups())
                    if option in options:
                        options_applied.append(option)
                        if self.padding:
                            self.copy_lines_cached[index] = "%s%s %s\n" % (option, equivalence.rstrip(" "), str(self.options.pop(option)))
                        else:
                            self.copy_lines_cached[index] = "%s%s%s\n" % (option, equivalence, str(self.options.pop(option)))

                    # Comment out any expected options that have already been set previously to prevent duplicates
                    elif not re.search("^%s" % self.comment_start, line) and option in options_applied:
                        self.copy_lines_cached[index] = "%s%s\n" % (self.comment_start, line)

            # Check for whether any options were not used. If so, insert them into a comment block and issue a warning.
            if self.options:
                self.copy_lines_cached.append("\n")
                self.copy_lines_cached.append("%s BEGIN %s\n" % (self.comment_start, self.block_message))
                for option, value in self.options.items():
                    if self.padding:
                        self.copy_lines_cached.append("%s = %s\n" % (option, value))
                    else:
                        self.copy_lines_cached.append("%s=%s\n" % (option, value))
                self.copy_lines_cached.append("%s END %s\n" % (self.comment_start, self.block_message))
                self.copy_lines_cached.append("\n")
                self.module.warn("Warning! Option(s) were not found and have been placed within a comment block at the end of the configuration file. Option(s) not found: [%s]" % ", ".join(self.options))

        return self.copy_lines_cached

    def check_source(self):
        """Ensure an original configuration file exists."""
        if self.path is not None:
            if not exists(self.path) and not isfile(self.path):
                self.module.fail_json(msg="Invalid configuration path was provided! path: %s" % self.path)

            # Create a read-only copy of source if path was provide and one didn't exist.
            if not exists(self.src):
                try:
                    read_fh = open(self.path, "r")
                    try:
                        write_fh = open(self.src, "w")
                        write_fh.writelines(read_fh.readlines())
                        write_fh.close()
                        chmod(self.src, int("0o%s" % self.mode, 8))
                    except Exception as error:
                        self.module.fail_json(msg="Failed to create copy of the original! Source: [%s]. Destination: [%s]. Error [%s]." % (self.src, self.dest, error))
                except Exception as error:
                    self.module.fail_json(msg="Failed to open source file! Source [%s]. Error [%s]." % (self.path, error))

        elif not exists(self.src) and not isfile(self.src):
            self.module.fail_json(msg="Invalid configuration path was provided! src: %s" % self.src)

    @property
    def update_required(self):
        """Determine whether an update is required."""
        if self.update_required_cached is None:
            self.check_source()

            self.update_required_cached = False
            if exists(self.dest):
                with open(self.dest, "r") as fh:
                    self.update_required_cached = self.updated_copy != fh.readlines()

                if self.mode:
                    mode = oct(stat(self.dest).st_mode)[-1 * len(self.mode):]
                    if self.mode != mode:
                        update_required_cached = True
            else:
                self.update_required_cached = True

        return self.update_required_cached

    def write_copy(self):
        """Write copy to the destination."""
        with open(self.dest, "w") as fh:
            fh.writelines(self.updated_copy)

        if self.mode:
            chmod(self.dest, int("0o%s" % self.mode, 8))

    def apply(self):
        """Determine and apply any required change to a copy of the source conf file."""
        if self.update_required and not self.module.check_mode:
            self.write_copy()

        self.module.exit_json(changed=self.update_required)

def main():
    update_conf = UpdateConfigFile()
    update_conf.apply()


if __name__ == "__main__":
    main()
