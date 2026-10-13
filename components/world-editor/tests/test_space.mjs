import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {DraftHistory,editableShape} from '../web/editor-state.mjs';
import {spaceCopy,spacePaste,removeSpace,translateSpace,localPoint,spaceSvg,objectArt} from '../web/space.mjs';
const fresh=()=>JSON.parse(readFileSync(new URL('../examples/cafe.nyairo-world.json',import.meta.url)));
test('copy wall remaps attached door and window; one undo restores entire spatial edit',()=>{
  const p=fresh(),h=new DraftHistory(p),room=p.rooms[0],clip=spaceCopy(room,'walls','partition');let n=0;
  const id=spacePaste(room,clip,()=>`copied-${++n}`);h.record(p);
  assert.equal(room.space.doors.at(-1).wall_id,id);assert.equal(room.space.windows.at(-1).wall_id,id);
  assert.notEqual(room.space.doors.at(-1).id,'waterbar-door');
  assert.equal(h.undo().rooms[0].space.walls.length,1);
  const pasted=h.redo();removeSpace(pasted.rooms[0],'walls',id);h.record(pasted);
  assert.equal(pasted.rooms[0].space.doors.length,1);assert.equal(pasted.rooms[0].space.windows.length,1);
  assert.equal(h.undo().rooms[0].space.doors.length,2);
});
test('room-local snapping and movement clamp geometry and attached span',()=>{
  const r=fresh().rooms[0];assert.deepEqual(localPoint(r,{x:r.x+r.w*.517,y:r.y-20}),{x:50,y:0});
  assert.equal(translateSpace(r,'walls',r.space.walls[0],100,100).x1,100);
  assert.equal(translateSpace(r,'doors',r.space.doors[0],0,100).offset,85);
  assert.equal(translateSpace(r,'floors',r.space.floors[0],100,100).x,50);
});
test('recovery shape accepts new space and pictures but rejects malformed references or behavior',()=>{
  const p=fresh();assert.equal(editableShape(p),true);
  p.rooms[0].space.doors[0].wall_id='missing';assert.equal(editableShape(p),false);
  const q=fresh();q.objects[1].behavior.fill=null;assert.equal(editableShape(q),false);
  const s=fresh();s.objects[2].asset_id='missing';assert.equal(editableShape(s),false);
});
test('render honors layers and actual door state, embeds reused custom picture',()=>{
  const p=fresh(),r=p.rooms[0];assert.equal(spaceSvg(r,p,null,null,{walls:false},'walls',true).includes('data-space="walls"'),false);
  const art=spaceSvg(r,p,{doors:{cafe:{'waterbar-door':'open'}}},null,{walls:true},'walls',false);
  assert.match(art,/data-door-state="open"/);
  assert.match(objectArt(p,p.objects[2],{}),/data:image\/svg\+xml;base64/);
});
