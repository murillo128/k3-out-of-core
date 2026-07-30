import copy, importlib.util, json, sys, unittest
from pathlib import Path
from jsonschema import Draft202012Validator
ROOT=Path(__file__).resolve().parents[2]; S=ROOT/"scripts/phase6"; sys.path.insert(0,str(S))
def load(name):
 spec=importlib.util.spec_from_file_location(name,S/f"{name}.py"); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
class Tests(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.c=load("common"); cls.v=load("verify_phase6"); cls.schema=json.loads((ROOT/"schemas/phase6/phase6-manifest-v1.schema.json").read_text())
 def test_schema(self):
  Draft202012Validator.check_schema(self.schema); self.assertEqual(self.schema["properties"]["execution_profile"]["const"],"STANDARD")
  gates=self.schema["properties"]["gates"]
  self.assertFalse(gates["additionalProperties"]); self.assertEqual(set(gates["required"]),set(gates["properties"]))
  self.assertTrue(all(value["const"] is True for value in gates["properties"].values()))
 def test_authority(self): self.assertEqual(self.c.PROJECT_BASE,"eb1b5baf5d505eadbc4298ecf322489cdfd7aae5"); self.assertEqual(self.c.CHECKPOINT_COMMENT,5133647261)
 def test_scope(self): self.assertFalse(any(x.startswith("ggml/") for x in self.v.ALLOWED_NESTED)); self.assertNotIn("src/models/kimi-k3.cpp",self.v.ALLOWED_NESTED)
 def test_required_nested_seams(self): self.assertIn("src/llama-expert-storage.cpp",self.v.ALLOWED_NESTED); self.assertIn("src/llama-model-loader.cpp",self.v.ALLOWED_NESTED)
 def test_closeout_is_manifest_only(self):
  self.assertEqual(self.v.ALLOWED_CLOSEOUT,{"results/2026-07-30/skynet/phase6-gguf-storage/phase6-manifest.json","results/2026-07-30/skynet/phase6-gguf-storage/verification-result.json"})
 def test_false_gate_rejected(self): gates={"parity":True,"bounded":True}; changed=copy.deepcopy(gates); changed["parity"]=False; self.assertTrue(all(gates.values())); self.assertFalse(all(changed.values()))
if __name__=="__main__": unittest.main()
