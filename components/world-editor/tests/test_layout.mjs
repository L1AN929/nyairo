import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {ref,entries,center,translate,resize,boxSelect,copySelection,paste,arrange} from '../web/layout.mjs';
import {DraftHistory,editableShape} from '../web/editor-state.mjs';
const fixture=JSON.parse(readFileSync(new URL('../examples/rainy-bookstore.nyairo-world.json',import.meta.url)));
const fresh=()=>structuredClone(fixture);
test('group room movement preserves relative spacing, clamps together, and moves contents once',()=>{
  const p=fresh(),rs=new Set(p.rooms.map(r=>ref('room',r.id)));rs.add(ref('object',p.objects[0].id));
  const n=translate(p,rs,900,900);assert.equal(n.rooms[1].x-n.rooms[0].x,p.rooms[1].x-p.rooms[0].x);
  assert.equal(Math.max(...n.rooms.map(r=>r.x+r.w)),1000);assert.equal(n.objects[0].x,p.objects[0].x);assert.deepEqual(p,fixture);
});
test('group objects cross rooms atomically; outside drop cannot partially move the group',()=>{
  const p=fresh(),keys=new Set([ref('object','old-book'),ref('object','tea-cup')]);
  const n=translate(p,keys,420,0);assert.equal(n.objects.find(o=>o.id==='old-book').room_id,'upstairs');assert.equal(n.objects.find(o=>o.id==='tea-cup').room_id,'upstairs');
  assert.equal(translate(p,keys,1000,0),null);assert.deepEqual(p,fixture);
});
test('resize snaps dimensions and respects minimum and map bounds',()=>{
  const r={x:200,y:100,w:200,h:100};assert.deepEqual(resize(r,18,33,25),{w:225,h:125});assert.deepEqual(resize(r,-999,-999),{w:100,h:80});assert.deepEqual(resize(r,999,999),{w:800,h:520});
});
test('box selection uses centers and separates object/room selection',()=>{
  const p=fresh(),r=p.rooms[0];const box={x:r.x,y:r.y,w:r.w,h:r.h};assert.equal(boxSelect(p,box,'object').length,4);assert.deepEqual(boxSelect(p,box,'room'),[ref('room',r.id)]);
});
test('copy/paste a connected region group remaps contents and internal edges with fresh IDs',()=>{
  const p=fresh(),keys=new Set(p.rooms.map(r=>ref('room',r.id))),clip=copySelection(p,keys),result=paste(p,clip),n=result.package;
  assert.equal(n.rooms.length,4);assert.equal(n.objects.length,12);assert.equal(n.connections.length,2);
  const ids=new Set(p.rooms.map(r=>r.id));assert.ok(n.rooms.slice(2).every(r=>!ids.has(r.id)));assert.ok(n.objects.slice(6).every(o=>n.rooms.slice(2).some(r=>r.id===o.room_id)));
  assert.ok(n.connections.slice(1).every(e=>!ids.has(e.from)&&!ids.has(e.to)));assert.equal(editableShape(n),true);
  const h=new DraftHistory(p);h.record(n);assert.deepEqual(h.undo(),p);assert.deepEqual(h.redo(),n);
});
test('object paste can target another existing region and preserves visual and behavior config',()=>{
  const p=fresh(),clip=copySelection(p,new Set([ref('object','old-book')])),n=paste(p,clip,'upstairs').package,o=n.objects.at(-1);
  assert.equal(o.room_id,'upstairs');assert.equal(o.behavior.read.minutes,21);assert.deepEqual(o.visual,p.objects.find(o=>o.id==='old-book').visual);
});
test('copying a region includes its contents but not its external connection',()=>{
  const p=fresh(),clip=copySelection(p,new Set([ref('room','bookstore')]));assert.equal(clip.objects.length,4);assert.equal(clip.connections.length,0);
});
test('align and distribute object centers in the same region; reject mixed or cross-region sets',()=>{
  const p=fresh(),keys=new Set(['reading-chair','old-book','tea-cup'].map(id=>ref('object',id))),n=arrange(p,keys,'horizontal');
  const xs=entries(n,keys).map(e=>center(n,e).x).sort((a,b)=>a-b);assert.ok(Math.abs((xs[1]-xs[0])-(xs[2]-xs[1]))<5);
  const aligned=arrange(p,keys,'top');assert.equal(new Set(entries(aligned,keys).map(e=>e.item.y)).size,1);
  assert.throws(()=>arrange(p,new Set([ref('room','bookstore'),ref('object','old-book')]),'left'),/分别/);
  assert.throws(()=>arrange(p,new Set([ref('object','old-book'),ref('object','soft-bed')]),'left'),/同一区域/);
});
test('room alignment preserves contents and package boundaries',()=>{
  const p=fresh(),keys=new Set(p.rooms.map(r=>ref('room',r.id))),n=arrange(p,keys,'top');assert.equal(n.rooms[0].y,n.rooms[1].y);assert.deepEqual(n.objects,p.objects);
});
