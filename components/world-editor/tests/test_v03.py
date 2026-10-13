import legacy
import copy
import json
import tempfile
import unittest
from pathlib import Path
from model import FORMAT, SAVE_FORMAT, LEGACY_SAVE, WorldError, digest, validate_package, validate_save, cover_svg
from server import ROOT
from store import Store

class VisualRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.legacy=json.loads((ROOT/'examples/legacy-v1-bookstore.nyairo-world.json').read_text(encoding='utf-8'))
        self.p=validate_package(json.loads((ROOT/'examples/rainy-bookstore.nyairo-world.json').read_text(encoding='utf-8')))[0]
        self.db=Path(self.tmp.name)/'editor.sqlite3';self.store=Store(self.db,self.p)
    def tearDown(self):self.tmp.cleanup()
    def test_read_rest_drink_use_configured_parameters_and_save_restore_them(self):
        t=self.store.create_trial(self.p)
        def act(v,target=None):
            nonlocal t
            t=self.store.execute(t['id'],'op-'+str(t['state']['revision']),t['state']['revision'],{'action':v,**({'target':target} if target else {})});return t
        act('pick_up','old-book');act('read','old-book')
        self.assertEqual(t['state']['minutes'],22);self.assertEqual(t['state']['resident']['energy'],60)
        saved=self.store.export_save(t['id']);restored=self.store.create_trial(save=saved)
        self.assertEqual(restored['package']['objects'][1]['behavior']['read']['minutes'],21)
        self.assertEqual(restored['state'],t['state'])
        act('place','old-book');act('pick_up','tea-cup');act('drink','tea-cup')
        self.assertEqual(t['state']['resident']['hydration'],85);self.assertEqual(t['state']['objects']['tea-cup']['servings'],1)
        act('place','tea-cup');act('move','upstairs');act('rest','soft-bed')
        self.assertEqual(t['state']['minutes'],76);self.assertEqual(t['state']['resident']['energy'],78)
        self.assertEqual(t['state']['resident']['pose'],'lying')
        self.assertEqual(Store(self.db,self.p).trial(t['id'])['state'],t['state'])
    def test_invalid_parameters_and_visual_fields_are_rejected(self):
        for change in (lambda p:p['objects'][1]['behavior']['read'].update(minutes=0),lambda p:p['objects'][1]['behavior']['read'].update(energy_cost=101),lambda p:p['objects'][1]['behavior'].update(script={}),lambda p:p['rooms'][0]['visual'].update(floor='url(http://x)'),lambda p:p['objects'][0]['visual'].update(scale=201),lambda p:p['objects'][0]['actions'].append('read')):
            p=copy.deepcopy(self.p);change(p)
            with self.assertRaises(WorldError):validate_package(p)
    def test_old_world_upgrade_is_exact_for_rules_and_layout(self):
        p,_=validate_package(self.legacy);self.assertEqual(p['format'],FORMAT)
        self.assertEqual(p['objects'][1]['behavior']['read'],{'minutes':15,'energy_cost':3})
        self.assertEqual(p['rooms'][0]['x'],self.legacy['rooms'][0]['x']);self.assertNotIn('visual',self.legacy['rooms'][0])
    def test_old_save_hash_is_checked_before_upgrade_and_state_is_preserved(self):
        prior,_=legacy.validate_package(self.legacy);old_state,_=legacy.apply_action(prior,legacy.initial_state(prior),{'action':'pick_up','target':'old-book'})
        old={'format':LEGACY_SAVE,'package':self.legacy,'package_hash':digest(self.legacy),'state':old_state}
        p,state=validate_save(old);self.assertEqual(p['format'],FORMAT);self.assertEqual(state['minutes'],old_state['minutes']);self.assertEqual(state['events'],old_state['events']);self.assertEqual(state['resident']['held'],old_state['resident']['held'])
        imported=self.store.create_trial(save=old);self.assertEqual(imported['state']['resident']['held'],'old-book')
        old['package_hash']='0'*64
        with self.assertRaises(WorldError):validate_save(old)
    def test_legacy_database_upgrade_keeps_state_receipts_and_rejects_old_writer(self):
        t=self.store.create_trial(self.legacy);t=self.store.execute(t['id'],'first',0,{'action':'pick_up','target':'old-book'});identity=self.store.workspace_id()
        with self.store.connection() as db:
            db.execute('UPDATE draft SET package=?,revision=3',(json.dumps(self.legacy),));db.execute('UPDATE trial SET package=? WHERE id=?',(json.dumps(self.legacy),t['id']))
        upgraded=Store(self.db,self.p);self.assertEqual(upgraded.workspace_id(),identity);self.assertEqual(upgraded.draft()['revision'],4)
        self.assertEqual(upgraded.trial(t['id'])['state'],t['state']);self.assertEqual(upgraded.trial(t['id'])['package']['format'],FORMAT)
        replay=upgraded.execute(t['id'],'first',0,{'action':'pick_up','target':'old-book'});self.assertTrue(replay['replayed']);self.assertEqual(replay['state'],t['state'])
        with self.assertRaises(WorldError):upgraded.save_draft(self.legacy,3)
    def test_visual_roundtrip_and_cover_embed_theme_and_sprite_without_external_paths(self):
        self.store.save_draft(self.p,0);self.assertEqual(Store(self.db,self.p).draft()['package'],self.p)
        svg=cover_svg(self.p).decode();self.assertIn(self.p['rooms'][0]['visual']['floor'],svg);self.assertIn('rotate(15)',svg);self.assertIn('scale(1.2)',svg);self.assertNotIn('<script',svg);self.assertNotIn('file:',svg)
    def test_durable_recovery_keeps_unfinished_fields_and_survives_restart(self):
        draft=copy.deepcopy(self.p);draft['meta']['version']='';draft['initial']['time']=''
        self.store.recovery_write({'client_id':'tab-a','sequence':1,'package':draft,'base_revision':0,'base_fingerprint':json.dumps(self.p)})
        restarted=Store(self.db,self.p);copy_saved=restarted.recoveries()[0]
        self.assertEqual(copy_saved['package'],draft);self.assertEqual(restarted.draft()['package'],self.p)
        self.assertEqual(copy_saved['baseRevision'],0)
    def test_recovery_sequence_blocks_late_write_after_clear_and_tabs_do_not_clobber(self):
        def write(client,seq,p):return self.store.recovery_write({'client_id':client,'sequence':seq,'package':p,'base_revision':0,'base_fingerprint':'base'})
        write('tab-a',1,self.p);write('tab-b',1,self.p);write('tab-a',3,None);write('tab-a',2,self.p)
        self.assertEqual([r['client_id'] for r in self.store.recoveries()],['tab-b'])
        write('tab-a',4,self.p);self.assertEqual(len(self.store.recoveries()),2)
