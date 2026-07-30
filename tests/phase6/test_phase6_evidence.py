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
 def test_missing_check_key_rejected(self):
  record={"checks":{key:True for key in self.v.BUNDLE_CHECKS}}; self.assertTrue(self.v.exact_checks(record,self.v.BUNDLE_CHECKS))
  del record["checks"]["sha256_exact"]; self.assertFalse(self.v.exact_checks(record,self.v.BUNDLE_CHECKS))
 def test_altered_validation_command_rejected(self):
  records=[{"name":name,"command":command,"exit_code":0} for name,command in self.v.EXPECTED_VALIDATION]
  self.assertTrue(self.v.exact_validation(records)); records[0]["command"]=["true"]
  self.assertFalse(self.v.exact_validation(records))
 def test_missing_sanitizer_validation_rejected(self):
  records=[{"name":name,"command":command,"exit_code":0} for name,command in self.v.EXPECTED_VALIDATION if name!="ctest-sanitizers"]
  self.assertFalse(self.v.exact_validation(records))
 def bundle(self):
  spans=[{"split_index":1,"offset":10,"count":2},{"split_index":2,"offset":20,"count":3},{"split_index":3,"offset":30,"count":4}]
  return {"kind":"split","checks":{key:True for key in self.v.BUNDLE_CHECKS},"bundle":{"bytes":9,"spans":spans,"distinct_split_indices":[1,2,3],"source_sha256":"a"*64,"cold_dump_sha256":"a"*64}}
 def test_collapsed_split_bundle_rejected(self):
  record=self.bundle(); self.assertTrue(self.v.bundle_case_valid(record))
  for span in record["bundle"]["spans"]: span["split_index"]=1
  record["bundle"]["distinct_split_indices"]=[1]; self.assertFalse(self.v.bundle_case_valid(record))
 def test_altered_digest_rejected(self):
  record=self.bundle(); record["bundle"]["cold_dump_sha256"]="b"*64; self.assertFalse(self.v.bundle_case_valid(record))
 def test_unbalanced_handles_rejected(self):
  record={"checks":{key:True for key in self.v.HANDLE_CHECKS},"diagnostics":{"supported":1,"baseline":6,"peak":8,"final":7,"balanced":1}}
  self.assertFalse(self.v.handle_lifetime_valid(record))
 def test_administration_overrun_rejected(self):
  diagnostics={"storage_files":218,"storage_entries":56,"storage_spans":168}
  diagnostics["storage_admin_upper_bound"]=65536+218*128+56*64+168*64
  diagnostics["storage_admin_bytes"]=diagnostics["storage_admin_upper_bound"]+1
  self.assertFalse(self.v.administration_bounded(diagnostics))
if __name__=="__main__": unittest.main()
