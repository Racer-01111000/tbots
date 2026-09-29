import sys, json
sys.path.insert(0, "scripts")
import fitness_v2_protocol as proto

kwargs = json.load(open("/tmp/shock_amendment_kwargs.json"))
kwargs["gap_extrema_bounds"] = tuple(kwargs["gap_extrema_bounds"])

new_envelope = proto.complete_protocol_manifest(**kwargs)
assert proto.validate_complete_protocol(new_envelope) == new_envelope["manifest_id"]

old_path = ("evolution/protocol/fitness_v2_complete_protocol_"
            "49c71da11d46f56838a560481350cb3436af3177bab6cb13254f9c84860c6e89.json")
old_content = json.loads(open(old_path).read())["content"]
new_content = new_envelope["content"]

print("NEW manifest_id:", new_envelope["manifest_id"])
print("--- field-level diff (old vs new) ---")
for k in sorted(set(old_content) | set(new_content)):
    if old_content.get(k) != new_content.get(k):
        print(k, ": OLD=", repr(old_content.get(k))[:150], " NEW=", repr(new_content.get(k))[:250])

new_path = "evolution/protocol/" + new_envelope["manifest_id"] + ".json"
with open(new_path, "w") as f:
    json.dump(new_envelope, f, indent=2, sort_keys=True)
    f.write("\n")
print("written to", new_path)
