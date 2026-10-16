import hashlib,json,pathlib,sqlite3,tempfile,unittest,zipfile
from backup_integrity import verify_bundle
class BundleTests(unittest.TestCase):
    def bundle(self,root,count=1,bad_hash=False):
        p=root/'db.sqlite3';db=sqlite3.connect(p);db.execute('CREATE TABLE candles(x)');db.execute('INSERT INTO candles VALUES (1)');db.commit();db.close();data=p.read_bytes()
        manifest={'rows':{'candles':count},'files':{'normalized/market.sqlite3':{'sha256':'wrong' if bad_hash else hashlib.sha256(data).hexdigest(),'bytes':len(data)}}}
        target=root/'test.zip'
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('normalized/market.sqlite3',data);z.writestr('manifest.json',json.dumps(manifest))
        return target
    def test_success_restore_is_cleaned(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d);m=verify_bundle(self.bundle(root),root/'restore.sqlite3');self.assertEqual(m['rows']['candles'],1);self.assertFalse((root/'restore.sqlite3').exists())
    def test_tamper_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            with self.assertRaises(ValueError):verify_bundle(self.bundle(root,bad_hash=True),root/'restore.sqlite3')
    def test_count_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d)
            with self.assertRaises(ValueError):verify_bundle(self.bundle(root,count=2),root/'restore.sqlite3')
if __name__=='__main__':unittest.main()
