import json

path = "/mnt/c/Users/montie01/PROJECTS/CAMRIE-app-1/calculation/task.json"
with open(path) as f:
    t = json.load(f)

for seq in t["task"]["options"]["sequences"]:
    seq["geometry"]["isocenter_mm"] = None

with open(path, "w") as f:
    json.dump(t, f, indent=4)

print("isocenter_mm set to null in both sequences")
