import {test} from 'node:test'
import assert from 'node:assert/strict'
import {narrationSegmentAt,followNarration} from '../src/narrationFollow.js'

const rows=[{index:1,start:0,end:2,pause_after:1},{index:2,start:3,end:5}]
test('saved timeline, pauses, boundaries and end',()=>{
  assert.equal(narrationSegmentAt(rows,0),1)
  assert.equal(narrationSegmentAt(rows,2.5),1)
  assert.equal(narrationSegmentAt(rows,3),2)
  assert.equal(narrationSegmentAt(rows,5),null)
  assert.equal(narrationSegmentAt(rows,NaN),null)
  assert.equal(narrationSegmentAt([],1),null)
})
function fixture(){
  const list={scrollTop:10,clientTop:1,getBoundingClientRect:()=>({top:100}),querySelectorAll:()=>[
    {dataset:{narrationIndex:'1'},getBoundingClientRect:()=>({top:120})},
    {dataset:{narrationIndex:'2'},getBoundingClientRect:()=>({top:250})}]}
  const event=(type,time,paused=false)=>({type,currentTarget:{currentTime:time,paused,ended:false}})
  return {list,event}
}
test('play follows new sentences without locking manual scroll',()=>{
  const {list,event}=fixture()
  assert.equal(followNarration(event('play',0),rows,null,list),1)
  assert.equal(list.scrollTop,29)
  list.scrollTop=50
  assert.equal(followNarration(event('timeupdate',1),rows,1,list),1)
  assert.equal(list.scrollTop,50)
  assert.equal(followNarration(event('timeupdate',3.2),rows,1,list),2)
  assert.equal(list.scrollTop,199)
})
test('paused time updates do not scroll; explicit paused scrub does',()=>{
  const {list,event}=fixture()
  assert.equal(followNarration(event('timeupdate',3.2,true),rows,1,list),1)
  assert.equal(list.scrollTop,10)
  assert.equal(followNarration(event('seeked',3.2,true),rows,1,list),2)
  assert.equal(list.scrollTop,159)
})
test('editing sentence follows play, sentence changes and scrubs, not paused updates',()=>{
  const {list,event}=fixture(), selected=[]
  const select=index=>selected.push(index)
  followNarration(event('play',0),rows,null,list,select)
  followNarration(event('timeupdate',1),rows,1,list,select)
  followNarration(event('timeupdate',3.2),rows,1,list,select)
  followNarration(event('timeupdate',0,true),rows,2,list,select)
  followNarration(event('seeked',0,true),rows,2,list,select)
  assert.deepEqual(selected,[1,2,1])
})
