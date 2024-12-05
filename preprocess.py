#! /usr/bin/env python3

import pathlib
import argparse
import tempfile
import pathlib
import re
import os
import shutil
import json
import subprocess
from typing import Union


def collect_bids_part(bids_part: str, path_like: Union[str, pathlib.Path]) -> str:
    """
    Regex is hard, this finds a bids key if it's present in path or pathlink string

    >>> bids_like_path = '/home/users/user/bids_data/sub-NDAR123/ses-firstsession'
    >>> subject_id = collect_bids_part('sub', bids_like_path)
    >>> subject_id
    >>> "sub-NDAR123"

    :param bids_part: the bids part to find in the path e.g. subject id, session id, recording, etc etc
    :type bids_part: string
    :param path_like: a pathlike input, this is strictly for parsing file paths or file like strings
    :type path_like: string or pathlib.Path object, we don't discriminate
    :return: the collected bids part
    :rtype: string
    """
    # get os of system
    if os.name == "posix":
        not_windows = True
    else:
        not_windows = False

    # break up path into parts
    parts = pathlib.PurePath(path_like).parts

    # this shouldn't happen, but we check if someone passed a windows path to a posix machine
    # should we check for the inverse? No, not until someone complains about this loudly enough.
    for part in parts:
        if "\\" in part and not_windows:
            # explicitly use windows path splitting
            parts = pathlib.PureWindowsPath(path_like).parts
            print(
                f"Detected \\ in BIDS like path {path_like}, but os is {os.name}, doing best to parse."
            )
            break

    # create search string
    search_string = bids_part + "-(.*)"
    # collect bids_part
    for part in parts:
        found_part = re.search(search_string, part)
        if found_part:
            collected_part = found_part[0]
            break
        else:
            collected_part = ""

    if "_" in collected_part:
        parts = collected_part.split("_")
        for part in parts:
            found_part = re.search(search_string, part)
            if found_part:
                collected_part = found_part[0]
                break
            else:
                collected_part = ""

    return collected_part


parser = argparse.ArgumentParser()

parser.add_argument(
    "--t1_file",
    "-t",
    type=Union[str],
    required=True,
    help="Path to the T1w anatomical file to be used for defacing",
)
parser.add_argument(
    "--pet_file",
    "-p",
    type=Union[str],
    required=True,
    help="Path to PET image to deface using T1w image as registration",
)

args = parser.parse_args()

t1_file = pathlib.Path(args.t1_file)
pet_file = pathlib.Path(args.pet_file)
# make sure there is a pet json associated with the pet file by replacing .nii or .nii.gz suffixes with .json
if pet_file.suffix == ".gz":
    pet_file_json = pet_file.with_suffix("").with_suffix(".json")
else:
    pet_file_json = pet_file.with_suffix(".json")

# collect the entities following the subject and potentially session ids from both the pet and t1 files
t1_subject_id = collect_bids_part("sub", t1_file)
pet_subject_id = collect_bids_part("sub", pet_file)
t1_session_id = None
pet_session_id = None

if not t1_subject_id and not pet_subject_id:
    subject_id = "temporarydefacee"
    print(
        "No BIDS subject id located, creating a temporary id {subject_id} for processing."
    )
elif t1_subject_id != pet_subject_id:
    raise Exception(
        f"Subject id's for t1w file {t1_file} and PET file {pet_file} do not match, exiting."
    )
else:
    subject_id = t1_subject_id
    t1_session_id = collect_bids_part("ses", t1_file)
    pet_session_id = collect_bids_part("ses", pet_file)

# collect the parent path to this script
app_petdeface_path = pathlib.Path(__file__).parent.resolve()

# open a temporary BIDS directory for running the pipeline
with tempfile.TemporaryDirectory(dir=app_petdeface_path) as tempdir:
    shutil.copy(app_petdeface_path / "dataset_description.json", tempdir)
    shutil.copy(app_petdeface_path / "README.md", pathlib.Path(tempdir) / "README")
    # create a subject directory in the tempdir
    subject_dir = pathlib.Path(pathlib.Path(tempdir) / subject_id)
    if subject_dir.exists():
        subject_dir.rmdir()
    else:
        subject_dir.mkdir(parents=True)

    # create any sessions folders that may exist
    if t1_session_id:
        t1_dir = subject_dir / t1_session_id / "anat/"
    else:
        t1_dir = subject_dir / "anat/"

    t1_dir.mkdir()
    shutil.copy(t1_file, t1_dir)

    if pet_session_id:
        pet_dir = subject_dir / pet_session_id / "pet/"
    else:
        pet_dir = subject_dir / "pet/"

    pet_dir.mkdir(parents=True)
    shutil.copy(pet_file, pet_dir)
    shutil.copy(pet_file_json, pet_dir)

    print("debug")

    # run the pet defacing pipeline
    #! /bin/bash

    # collect environment variables as well as vars from config.json
    with open("config.json", "r") as f:
        config = json.load(f)
        n_procs = config.get("n_procs", "")
        placement = config.get("placement", "inplace")

    freesurfer_license = os.environ.get("FREESURFER_LICENSE", "")
    if not freesurfer_license:
        raise Exception("FREESURFER_LICENSE environment variable not set, exiting.")
    else:
        shutil.copy(freesurfer_license, app_petdeface_path / "license.txt")

    # build subprocess command
    command = [
        "singularity",
        "exec",
        "-e",
        "-B",
        f"{freesurfer_license}:/opt/freesurfer/license.txt",
        "docker://openneuropet/petdeface:latest",
        "petdeface",
        f"{tempdir}",
        "output",
        "--n_procs",
        f"{n_procs}",
        "--placement",
        f"{placement}",
        "--n_procs",
        f"{n_procs}",
    ]

    defacing = subprocess.run(command, check=True)

    # copy the defaced pet, defaced t1w, and the registration mask files back to their original locations
    #defaced_pet = pathlib.Path(output_dir) / subject_id / pet_session_id / "pet"
    #defaced_t1 = pathlib.Path(output_dir) / t1_dir / "anat" / t1_file.name
    # TODO copy over the defacing mask and restration files as well.
    #defacing_mask = pathlib.Path(output_dir) / 'derivatives' / 'petdeface' / subject_id / pet_session_id / "pet" / "defacing_mask.nii.gz"
    #shutil.copy(defaced_pet, pet_file)
    #shutil.copy(defaced_pet.with_suffix(".json"), pet_file.with_suffix(".json"))
    #shutil.copy()
