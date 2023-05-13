import os
import docker, re, json, hashlib

from typing import Optional, List, Dict, Any, Tuple, Union

client = docker.from_env()

with open("config.json", encoding="utf-8") as f:
    CONFIG = json.load(f)

def remove_ansi_escape_sequences(s):

    def remove_ansi_codes(s):
        ansi_escape = re.compile(r'''
            (\x9B|\x1B\[)[0-?]*[ -/]*[@-~]  # CSI sequence
            |                              # OR
            (\x1B\]).*?(\x07|\x1B\\)       # OSC sequence
        ''', re.VERBOSE)
        return ansi_escape.sub('', s)
    
    return remove_ansi_codes(s)

class JudgeConfig:
    image: str = CONFIG["docker"]["localhost"] + "/default"
    init_script: List[Tuple[str, str]] = None
    start: Tuple[str, str] = None
    description: str
    check: list = None
    match: dict = None
    example_script: str = None

    def get_evaluation_type(self):
        if self.check:
            return "check"
        elif self.match:
            return "match"

    def get_evaluation_content(self):
        return self.check or self.match

def load_config(config_path, problem_index, script_root_dir="."):
    def load_script(script_obj):
        if script_obj is None:
            return None
        if type(script_obj) is str:
            return "bash", script_obj
        if "language" not in script_obj:
            language = "bash"
        else:
            language = script_obj["language"]
        if "file" in script_obj:
            with open(os.path.join(script_root_dir, script_obj["file"]), encoding="utf-8") as f:
                return language, f.read()
        elif "code" in script_obj:
            return language, script_obj["code"]
        else:
            raise ValueError("Invalid Script Object")

    # 1. handle input file:
    if not os.path.exists(config_path):
        raise FileNotFoundError("Config File Not Found")
    
    if config_path.endswith(".json"):
        with open(config_path, encoding="utf-8") as f:
            config_raw = json.load(f)
        if isinstance(config_raw, list):
            pass
        elif isinstance(config_raw, dict):
            config_raw = [config_raw]
        else:
            raise ValueError("Invalid Config File")
    elif config_path.endswith(".jsonl"):
        with open(config_path, encoding="utf-8") as f:
            config_raw = [json.loads(line) for line in f.readlines()]
    else:
        raise ValueError("Invalid Config File")
    
    if len(config_raw) == 0:
        raise ValueError("Empty Config File")
    
    if len(config_raw) > 1:
        if problem_index is None:
            raise ValueError("Multiple Configs in Config File, You should specify problem_index")
    
    if problem_index is None:
        problem_index = 0
        
    if problem_index >= len(config_raw) or problem_index < 0:
        raise ValueError("Invalid problem_index, read %d, but [%d, %d] expected" % (problem_index, 0, len(config_raw) - 1))
    
    item = config_raw[problem_index]
    
    # 2. handle config
    config = JudgeConfig()
    config.description = item["description"]
    if "create" in item:
        if "image" in item["create"]:
            config.image = item["create"]["image"]
        if "init" in item["create"]:
            if type(item["create"]["init"]) is not list:
                config.init_script = [load_script(item["create"]["init"])]
            else:
                config.init_script = [load_script(script_obj) for script_obj in item["create"]["init"]]
        else:
            config.init_script = []
    else:
        config.image = CONFIG["docker"]["localhost"] + "/default"
    if "start" in item:
        config.start = load_script(item["start"])
    evaluation = item["evaluation"]
    if "match" in evaluation:
        if type(evaluation["match"]) is str:
            config.match = {
                "answer": evaluation["match"],
                "strip": True
            }
        else:
            config.match = evaluation["match"]
    elif "check" in evaluation:
        if type(evaluation["check"]) is not list:
            config.check = [load_script(evaluation["check"])]
        else:
            config.check = [load_script(script_obj) for script_obj in evaluation["check"]]
    else:
        raise ValueError("check or match must exist.")
    if "check" in evaluation and "example" in evaluation:
        config.example_script = load_script(evaluation["example"])
    return config


"""
This file is used to:
1. Create a docker container based on the problem file.
2. Run the init script.
3. Run the start script. (Manually)
4. Make some operations. (Manually)
5. Exit, and commit an answer. (Manually)
"""

def get_file_hash(file_path):
    """Function to get hash of a file"""
    hasher = hashlib.md5()
    with open(file_path, 'rb') as afile:
        buf = afile.read()
        hasher.update(buf)
    return hasher.hexdigest()

def execute_independent(command, *params):
    language, command = command
    print("EXECUTING ...")
    print(command)
    if params:
        print("== Parameters ==\n", params)
    if language == "bash":
        cmd = ["bash", "-c", command]
        if params:
            cmd.append("--")
            cmd.extend(params)
    elif language == "python":
        cmd = ["python3", "-c", command, *params]
    elif language == "c++":
        execute_independent(("bash", f"echo \"{json.dumps(command)}\" > /tmp/main.cpp && "
                                            f"g++ -o /tmp/a.out /tmp/main.cpp"), None)
        cmd = ["/tmp/a.out", *params]
    elif language == "c":
        execute_independent(("bash", f"echo \"{json.dumps(command)}\" > /tmp/main.cpp && "
                                            f"gcc -o /tmp/a.out /tmp/main.cpp"), None)
        cmd = ["/tmp/a.out", *params]
    else:
        raise ValueError("Unsupported language")
    return container.exec_run(cmd)

import argparse
if __name__ == "__main__":
    # Load specific problem config
    # use python judge.py <problem_path> <problem_index> to run a specific problem if <problem_path> contains multiple problems.
    # otherwise, use python judge.py <problem_path>
    parser = argparse.ArgumentParser()
    parser.add_argument("problem_path", type=str)
    parser.add_argument("problem_index", type=int, nargs="?", default=None)
    args = parser.parse_args()
    problem_path = args.problem_path
    problem_index = args.problem_index
    try:
        config = load_config(problem_path, problem_index, CONFIG["scripts"]["directory"])
    except Exception as e:
        print("Error Occurred when loading config.")
        print(str(e))
        exit(1)
    print("=== Problem ===")
    print(config.description)
    
    print("=== Step 1. Create a docker container based on the problem file. ===")
    dockerfile_directory = CONFIG["docker"]["directory"]
    if config.image.startswith(CONFIG["docker"]["localhost"]):
        print("Use a local image.")
        filename = os.path.basename(config.image)
        image_name = f'{CONFIG["docker"]["localhost"]}/{filename}'
        dockerfile_path = os.path.join(dockerfile_directory, filename)
        try:
            image = client.images.get(image_name)
            # Check if the dockerfile has changed
            if image.labels.get('file_hash') != get_file_hash(dockerfile_path):
                # If dockerfile has changed, rebuild image
                print(f'Dockerfile changed. Rebuilding image: {image_name}')
                client.images.build(path=dockerfile_directory, dockerfile=filename, tag=image_name, labels={'file_hash': get_file_hash(dockerfile_path)})
            else:
                print(f'Image: {image_name} up to date.')
        except docker.errors.ImageNotFound:
            # If image does not exist, build it
            print(f'Building image: {image_name}')
            client.images.build(path=dockerfile_directory, dockerfile=filename, tag=image_name, labels={'file_hash': get_file_hash(dockerfile_path)})
    
    print("=== Step 2. Run a docker container and init scripts ===")
    container = client.containers.run(config.image, detach=True, tty=True, stdin_open=True)
    if config.init_script:
        print("Running Init Scripts...")
        for script in config.init_script:
            execute_independent(script)
    
    print("=== Step 3. Run the container. (Please Run the start script manually, and solve the problem) ===")
    os.system("docker exec -it %s bash" % container.id)
    
    print("=== Step 4. Commit an answer if needed. ===")
    answer = input("[Answer | Enter to Skip] >>> ").strip()
    
    print("=== Step 5. Check the answer. ===")

    result = False
    
    if config.match:
        if "answer" in config.match:
            result = answer == config.match["answer"]
        elif "regex" in config.match:
            result = re.search(config.match["regex"], answer) is not None
    elif config.check:
        params = [answer]
        for script in config.check:
            if script is None:
                script = config.example_script
            response = execute_independent(script, *params)
            if response.exit_code != 0:
                print("Exit Code:", response.exit_code, "\nOutput:", response.output.decode("utf-8"))
                result = False
                break
            params.append(response.output.decode("utf-8"))
        else:
            result = True
    else:
        raise Exception("Invalid evaluation type in config")
    
    print("=== Result: %s ===" % result)