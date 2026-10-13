"""v0.4 acceptance: actual paths, atomic interaction chains and portable pictures."""
import base64
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
import legacy
from model import (WorldError, validate_package, validate_save, export_package,
                   initial_state, apply_action, digest, FORMAT, SAVE_FORMAT)
from resources import import_asset, picture
from server import ROOT
from space import path
from store import Store


class SpatialInteractionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = json.loads((ROOT/'examples/cafe.nyairo-world.json').read_text(encoding='utf-8'))
        self.store = Store(Path(self.tmp.name)/'editor.sqlite3', self.p)
        self.t = self.store.create_trial(self.p)

    def tearDown(self):
        self.tmp.cleanup()

    def act(self, action, target=None, **extra):
        command = {'action':action, **extra}
        if target is not None: command['target'] = target
        self.t = self.store.execute(self.t['id'], 'step-'+str(self.t['state']['revision']),
                                    self.t['state']['revision'], command)
        return self.t['state']

    def rejected(self, code, action, target=None, **extra):
        before = copy.deepcopy(self.t['state'])
        with self.assertRaises(WorldError) as caught:
            self.act(action, target, **extra)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.store.trial(self.t['id'])['state'], before)

    def cross(self):
        self.act('walk', x=8, y=10)
        self.act('open_door', 'cafe:waterbar-door')
        self.act('walk', x=14, y=11)

    def test_closed_locked_and_window_block_open_door_provides_actual_path(self):
        self.rejected('BLOCKED', 'walk', x=14, y=11)
        self.rejected('TOO_FAR', 'open_door', 'cafe:waterbar-door')
        self.act('walk', x=8, y=10)
        self.act('lock_door','cafe:waterbar-door')
        self.rejected('LOCKED','open_door','cafe:waterbar-door')
        self.rejected('BLOCKED','walk',x=14,y=11)
        self.act('unlock_door','cafe:waterbar-door')
        self.rejected('BLOCKED','walk',x=14,y=11)
        self.act('open_door','cafe:waterbar-door')
        s = self.act('walk',x=14,y=11)
        points=s['path']['points']
        self.assertEqual(points[0], {'x':8,'y':10})
        self.assertEqual(points[-1], {'x':14,'y':11})
        self.assertTrue(all(abs(a['x']-b['x'])+abs(a['y']-b['y'])==1 for a,b in zip(points,points[1:])))
        crossings=[a for a,b in zip(points,points[1:]) if a['x']!=b['x'] and {a['x'],b['x']}=={9,10}]
        self.assertTrue(crossings)
        self.assertTrue(all(45 <= a['y']*5+2.5 < 60 for a in crossings))
        space=copy.deepcopy(self.p['rooms'][0]['space']);space['doors']=[]
        self.assertIsNone(path(space,{},(8,3),(12,3)), 'window must not be a walkable doorway')
        space=copy.deepcopy(self.p['rooms'][0]['space'])
        space['walls'][0].update(x1=0,y1=50,x2=100,y2=50)
        self.assertIsNone(path(space,{'waterbar-door':'closed'},(10,8),(10,14)))
        self.assertIsNotNone(path(space,{'waterbar-door':'open'},(10,8),(10,14)))

    def test_invalid_spatial_geometry_opening_overlap_and_asset_reference_are_rejected(self):
        for change in [lambda s:s['walls'][0].update(x2=80),
                       lambda s:s['doors'][0].update(offset=95,length=15),
                       lambda s:s['windows'][0].update(offset=45),
                       lambda s:s['floors'][0].update(x=55,w=50),
                       lambda s:s['floors'][0].update(asset_id='missing'),
                       lambda s:s['walls'][0].update(y1=3)]:
            p=copy.deepcopy(self.p);change(p['rooms'][0]['space'])
            with self.assertRaises(WorldError):validate_package(p)

    def test_empty_fill_drink_chain_params_idempotency_and_restart(self):
        self.act('pick_up','empty-cup')
        self.rejected('EMPTY','drink','empty-cup')
        self.rejected('TOO_FAR','fill','water-dispenser')
        self.cross()
        before=self.t['state']['minutes']
        expected=self.t['state']['revision']
        self.act('fill','water-dispenser')
        filled=copy.deepcopy(self.t['state'])
        self.assertEqual(filled['objects']['empty-cup']['status'],'water')
        self.assertEqual(filled['objects']['empty-cup']['servings'],1)
        self.assertEqual(filled['minutes'],before+2)
        self.assertEqual(filled['objects']['water-dispenser']['use_count'],1)
        replay=self.store.execute(self.t['id'],'step-'+str(expected),expected,{'action':'fill','target':'water-dispenser'})
        self.assertTrue(replay['replayed']);self.assertEqual(replay['state'],filled)
        self.rejected('NOT_EMPTY','fill','water-dispenser')
        water=filled['resident']['hydration'];self.act('drink','empty-cup')
        self.assertEqual(self.t['state']['resident']['hydration'], min(100,water+20))
        self.assertEqual(self.t['state']['objects']['empty-cup']['status'],'empty')
        self.rejected('EMPTY','drink','empty-cup')
        self.assertEqual(self.store.create_trial(save=self.store.export_save(self.t['id']))['state'],self.t['state'])
        reopened=Store(self.store.path,self.p)
        self.assertEqual(reopened.trial(self.t['id'])['state'],self.t['state'])
        late=reopened.execute(self.t['id'],'step-'+str(expected),expected,{'action':'fill','target':'water-dispenser'})
        self.assertEqual(late['state'],self.t['state'], 'old receipt must not refill or rewind later experience')

    def test_missing_cup_device_state_disabled_rule_and_light_are_atomic(self):
        self.cross();self.rejected('NO_CUP','fill','water-dispenser')
        self.act('walk',x=5,y=11);self.act('pick_up','empty-cup');self.act('walk',x=14,y=11)
        self.act('toggle_device','water-dispenser')
        self.rejected('DISABLED','fill','water-dispenser')
        self.act('toggle_device','water-dispenser');self.act('fill','water-dispenser')
        self.act('walk',x=13,y=5);self.act('toggle','lamp')
        self.assertEqual(self.t['state']['objects']['lamp']['status'],'on')
        disabled=copy.deepcopy(self.p);disabled['objects'][1]['actions'].remove('fill')
        t=self.store.create_trial(disabled)
        state=copy.deepcopy(t['state']);state['resident']['position']={'x':14,'y':11}
        with self.assertRaises(WorldError) as c:apply_action(disabled,state,{'action':'fill','target':'water-dispenser'})
        self.assertEqual(c.exception.code,'NOT_ALLOWED')
        self.assertEqual(state['objects']['water-dispenser']['use_count'],0)

    def test_near_condition_uses_path_not_straight_line_and_graph_cannot_bypass_wall(self):
        s=initial_state(self.p);s['resident']['position']={'x':9,'y':11}
        p=copy.deepcopy(self.p);p['objects'][1]['x']=52;p['objects'][1]['interaction']['radius']=8
        s=initial_state(p);s['resident']['position']={'x':9,'y':11}
        with self.assertRaises(WorldError) as c:apply_action(p,s,{'action':'fill','target':'water-dispenser'})
        self.assertEqual(c.exception.code,'TOO_FAR')
        prior=json.loads((ROOT/'examples/rainy-bookstore.nyairo-world.json').read_text(encoding='utf-8'))
        p=validate_package(prior)[0];p['rooms'][0]['space']=copy.deepcopy(self.p['rooms'][0]['space'])
        s=initial_state(p);s['resident']['position']={'x':14,'y':11}
        with self.assertRaises(WorldError) as c:apply_action(p,s,{'action':'move','target':'upstairs'})
        self.assertEqual(c.exception.code,'BLOCKED')

    def test_save_rejects_contradictory_cup_bad_path_and_held_location(self):
        self.act('pick_up','empty-cup');self.cross();self.act('fill','water-dispenser')
        save=self.store.export_save(self.t['id'])
        for change in [lambda s:s['objects']['empty-cup'].update(status='empty'),
                       lambda s:s['objects']['empty-cup'].update(position={'x':0,'y':0}),
                       lambda s:s['path']['points'].append({'x':0,'y':0}),
                       lambda s:s['doors']['cafe'].update(forged='open')]:
            bad=copy.deepcopy(save);change(bad['state'])
            with self.assertRaises(WorldError):validate_save(bad)


class PortableResourceTests(unittest.TestCase):
    def asset(self, name, mime):
        data=base64.b64encode((ROOT/'tests/fixtures'/name).read_bytes()).decode()
        return import_asset({'mime':mime,'data':data,'name':name,'collection':'咖啡馆家具'})

    def test_real_png_webp_and_static_svg_import_without_filesystem_dependency(self):
        p=json.loads((ROOT/'examples/cafe.nyairo-world.json').read_text(encoding='utf-8'))
        with self.assertRaises(WorldError) as caught:legacy.validate_package(p)
        self.assertEqual(caught.exception.code,'INCOMPATIBLE', 'the actual old reader must reject v3')
        for name,mime in [('counter.png','image/png'),('counter.webp','image/webp')]:
            a=self.asset(name,mime);self.assertEqual((a['width'],a['height']),(64,48))
            p['assets'][a['id']]=a;p['objects'][2]['asset_id']=a['id']
            exported=export_package(p)
            self.assertEqual(list(exported['assets']),[a['id']], 'unused library must be pruned')
            with tempfile.TemporaryDirectory() as tmp:
                store=Store(Path(tmp)/'new.sqlite3',exported);store.save_draft(exported,0)
                self.assertEqual(store.draft()['package']['assets'][a['id']]['data'],a['data'])
                self.assertEqual(store.create_trial(exported)['package']['objects'][2]['asset_id'],a['id'])
        a=next(iter(p['assets'].values()));self.assertEqual(a['mime'],'image/svg+xml')

    def test_active_svg_and_external_or_damaged_images_rejected(self):
        for body in ['<script>alert(1)</script>','<image href="file:///c:/secret"/>',
                     '<rect onclick="x()"/>','<foreignObject/>','<animate/>',
                     '<rect fill="url(https://example.com/x)"/>','<style>rect{fill:red}</style>']:
            data=base64.b64encode(('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 48">'+body+'</svg>').encode()).decode()
            with self.assertRaises(WorldError):picture('image/svg+xml',data)
        png=bytearray((ROOT/'tests/fixtures/counter.png').read_bytes());png[-5]^=1
        with self.assertRaises(WorldError):picture('image/png',base64.b64encode(png).decode())
        with self.assertRaises(WorldError):picture('image/webp',base64.b64encode(b'not a webp').decode())
        with self.assertRaises(WorldError):picture('image/svg+xml','not base64!')

    def test_real_v2_save_database_upgrade_preserves_experience_and_receipt(self):
        p=json.loads((ROOT/'examples/rainy-bookstore.nyairo-world.json').read_text(encoding='utf-8'))
        s,event=legacy.apply_action(p,legacy.initial_state(p),{'action':'pick_up','target':'old-book'})
        s,event=legacy.apply_action(p,s,{'action':'read','target':'old-book'})
        save={'format':legacy.SAVE_FORMAT,'package':p,'package_hash':digest(p),'state':s}
        upgraded,state=validate_save(save)
        self.assertEqual(state['events'],s['events']);self.assertEqual(state['minutes'],22)
        self.assertEqual(state['resident']['energy'],60)
        self.assertFalse(upgraded['rooms'][0]['space']['enabled'])
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'old.sqlite3',p);identity=store.workspace_id()
            with store.connection() as db:
                db.execute('UPDATE draft SET revision=8,package=?',(legacy.canonical(p),))
                db.execute('INSERT INTO trial VALUES (?,?,?,?)',('old-trial',legacy.canonical(p),legacy.canonical(s),0))
                db.execute('INSERT INTO receipt VALUES (?,?,?,?)',('old-trial','old-read',legacy.canonical({'revision':1,'command':{'action':'read','target':'old-book'}}),legacy.canonical(event)))
            reopened=Store(store.path,p)
            self.assertEqual(reopened.workspace_id(),identity);self.assertEqual(reopened.draft()['revision'],9)
            self.assertEqual(reopened.trial('old-trial')['state'],state)
            replay=reopened.execute('old-trial','old-read',1,{'action':'read','target':'old-book'})
            self.assertTrue(replay['replayed']);self.assertEqual(replay['state']['minutes'],22)
            with self.assertRaises(WorldError):reopened.save_draft(p,8)

if __name__=='__main__':unittest.main()
