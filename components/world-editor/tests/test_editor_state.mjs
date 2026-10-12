import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DraftHistory, RecoveryStore, fingerprint, editableShape, moveRoom, moveObject} from '../web/editor-state.mjs';
const fixture=JSON.parse(readFileSync(new URL('../examples/legacy-v1-bookstore.nyairo-world.json',import.meta.url)));
const fresh=()=>structuredClone(fixture);
class MemoryStorage {
  data=new Map();
  get length(){return this.data.size;}
  key(i){return [...this.data.keys()][i]??null;}
  getItem(k){return this.data.get(k)??null;}
  setItem(k,v){this.data.set(k,String(v));}
  removeItem(k){this.data.delete(k);}
}
test('canonical dirty comparison ignores JSON object key order',()=>{
  const p=fresh();p.meta=Object.fromEntries(Object.entries(p.meta).reverse());
  assert.equal(fingerprint(p),fingerprint(fixture));
});
test('typing group undoes once, redo works and a new branch discards redo',()=>{
  const p=fresh(),h=new DraftHistory(p);
  p.meta.name='雨';h.record(p,'name');p.meta.name='雨夜';h.record(p,'name');
  assert.equal(h.undo().meta.name,fixture.meta.name);assert.equal(h.redo().meta.name,'雨夜');
  h.undo();p.meta.name='新世界';h.record(p);assert.equal(h.canRedo,false);
});
test('selection boundaries create separate field edits and snapshots do not alias',()=>{
  const p=fresh(),h=new DraftHistory(p);
  p.meta.name='一';h.record(p,'name');h.endGroup();p.meta.name='二';h.record(p,'name');
  const undo=h.undo();assert.equal(undo.meta.name,'一');undo.rooms[0].x=999;
  assert.equal(h.redo().rooms[0].x,fixture.rooms[0].x);
});
test('deleting a region and its contents restores the entire graph in one undo',()=>{
  const p=fresh(),h=new DraftHistory(p),id=p.rooms[1].id;
  p.rooms=p.rooms.filter(r=>r.id!==id);p.objects=p.objects.filter(o=>o.room_id!==id);
  p.connections=p.connections.filter(e=>e.from!==id&&e.to!==id);h.record(p);
  assert.deepEqual(h.undo(),fixture);assert.equal(h.redo().rooms.length,1);
});
test('history is bounded by count and memory; runtime state is outside its snapshots',()=>{
  const p=fresh(),h=new DraftHistory(p,{limit:2}),runtime={revision:7};
  for(let i=0;i<6;i++){p.meta.name=String(i);h.record(p);}
  assert.equal(h.past.length,2);h.undo();assert.equal(runtime.revision,7);
  const small=new DraftHistory(p,{maxBytes:1});p.meta.name='next';small.record(p);assert.equal(small.canUndo,false);
});
test('recovery keeps unfinished fields and isolates workspace and tab writers',()=>{
  const s=new MemoryStorage(),a=new RecoveryStore(s,'world-a','a'),b=new RecoveryStore(s,'world-a','b'),c=new RecoveryStore(s,'world-b','a');
  const p=fresh();p.meta.version='';p.initial.time='';assert.equal(editableShape(p),true);
  a.write(p,0,fingerprint(fixture),10);const q=fresh();q.meta.name='另一个窗口';b.write(q,0,fingerprint(fixture),20);
  assert.equal(a.candidates(fixture).length,2);assert.equal(a.candidates(fixture)[0].package.meta.name,'另一个窗口');
  assert.equal(c.candidates(fixture).length,0);a.clearOwn();assert.equal(b.candidates(fixture).length,1);
});
test('corrupt entries and dangling references cannot prevent opening other recovery copies',()=>{
  const s=new MemoryStorage(),r=new RecoveryStore(s,'w','a'),p=fresh();p.meta.name='恢复';r.write(p,0,fingerprint(fixture));
  s.setItem(r.prefix+'broken','{');const bad=fresh();bad.objects[0].room_id='missing';
  s.setItem(r.prefix+'bad',JSON.stringify({schema:1,package:bad,baseRevision:0,baseFingerprint:'x',updated:1}));
  assert.equal(r.candidates(fixture).length,1);assert.equal(r.candidates(p).length,0);
});
test('preserving a current draft survives replacement of the tab backup; quota failure is surfaced',()=>{
  const s=new MemoryStorage(),r=new RecoveryStore(s,'w','a'),p=fresh();p.meta.name='当前';r.preserve(p,1,'base');
  const q=fresh();q.meta.name='恢复内容';r.write(q,0,'old');assert.equal(r.candidates(fixture).length,2);
  const unavailable=new RecoveryStore({setItem(){throw new Error('quota');}},'w','a');assert.throws(()=>unavailable.write(p,0,'base'),/quota/);
});
test('old revision and base fingerprints remain available for conflict detection',()=>{
  const s=new MemoryStorage(),r=new RecoveryStore(s,'w','a'),p=fresh();p.meta.name='未保存';r.write(p,2,fingerprint(fixture));
  const newer=fresh();newer.meta.name='服务器新版';const b=r.candidates(newer)[0];
  assert.equal(b.baseRevision,2);assert.notEqual(b.baseFingerprint,fingerprint(newer));assert.equal(b.package.meta.name,'未保存');
});
test('a clean reopened document cannot clear an earlier unreviewed recovery copy',()=>{
  const s=new MemoryStorage(),old=new RecoveryStore(s,'w','old-document'),p=fresh();p.meta.name='还没恢复';old.write(p,0,fingerprint(fixture));
  const reopened=new RecoveryStore(s,'w','new-document');assert.equal(reopened.candidates(fixture).length,1);
  reopened.clearOwn();assert.equal(new RecoveryStore(s,'w','third-document').candidates(fixture)[0].package.meta.name,'还没恢复');
});
test('room movement snaps, clamps the whole rectangle, and rejects invalid geometry',()=>{
  const r={x:10,y:20,w:400,h:300};assert.deepEqual(moveRoom(r,13,12),{x:25,y:30});
  assert.deepEqual(moveRoom(r,999,999),{x:600,y:320});assert.deepEqual(moveRoom(r,-999,-999),{x:0,y:0});
  assert.deepEqual(moveRoom(r,1,1,false),{x:11,y:21});assert.equal(moveRoom({...r,w:1001},1,1),null);
  assert.equal(moveRoom(r,NaN,1),null);assert.equal(r.x,10);
});
test('object drop changes rooms, clamps label-safe positions and does not mutate on an outside drop',()=>{
  const p=fresh(),o=p.objects[0],r=p.rooms[1],before=structuredClone(o);
  assert.deepEqual(moveObject(p,o,{x:r.x+r.w/2,y:r.y+r.h/2}),{room_id:r.id,x:50,y:50});
  assert.deepEqual(moveObject(p,o,{x:r.x,y:r.y}),{room_id:r.id,x:5,y:15});
  assert.equal(moveObject(p,o,{x:-100,y:-100}),null);assert.deepEqual(o,before);
});
