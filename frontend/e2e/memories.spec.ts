import { expect,test } from '@playwright/test';

test('记忆查看证据、合并预览与撤销',async({page})=>{
  let merged=false;let version=2;
  const base={id:1,group_id:1,type:'topic',title:'AI 求职话题',summary:'讨论求职工具',status:'active',keywords:[],last_source_at:'2026-09-01T04:00:00Z'};
  const target={...base,id:2,title:'AI 工具与求职'};
  const detail=()=>({...base,version,merged_into_id:merged?2:null,merge_operation_id:merged?99:null,entries:[{id:4,summary:'有人推荐工具',observed_start:base.last_source_at,event_at:null,claims:[{key:'x',text:'推荐工具'}],sources:[{claim_key:'x',message_id:42,quote:'推荐 Claude Code',validation_state:'valid'}]}]});
  await page.route('**/api/**',async route=>{
    const p=new URL(route.request().url()).pathname;const json=(v:unknown)=>route.fulfill({contentType:'application/json',body:JSON.stringify(v)});
    if(p==='/api/system/ready')return json({ready:true,checks:{}});
    if(p==='/api/groups')return json([{id:1,display_name:'测试群'}]);
    if(p==='/api/v2/memories')return json({items:[detail(),target],next_offset:null});
    if(p==='/api/v2/knowledge/memory/status')return json({enabled:true,ai_enabled:false,limits:{calls:3,input:24000,output:4000},groups:[],operations:[]});
    if(p==='/api/v2/memories/1')return json(detail());
    if(p==='/api/v2/memories/merge-preview')return json({source:detail(),target,version:'a'.repeat(64),warnings:['原始证据保留']});
    if(p==='/api/v2/memories/merge'){expect(route.request().postDataJSON().expected_version).toBe('a'.repeat(64));merged=true;version++;return json({operation_id:99});}
    if(p==='/api/v2/memories/1/undo-merge'){expect(route.request().postDataJSON().operation_id).toBe(99);merged=false;version++;return json({operation_id:100});}
    if(p==='/api/v2/messages/42/context')return json({before:[],message:{id:42,content:'推荐 Claude Code',sender_name:'甲',sent_at:base.last_source_at},after:[]});
    if(p==='/api/v2/messages/42/sources')return json({items:[],next_offset:null});
    throw new Error(`Unexpected API ${p}`);
  });
  await page.goto('/#/memories?memory=1');
  const drawer=page.getByRole('complementary',{name:'记忆详情'});
  await drawer.getByRole('button',{name:'查看原消息 #42'}).click();
  await expect(page.getByRole('complementary',{name:'来源消息'})).toContainText('推荐 Claude Code');
  await page.getByRole('button',{name:'关闭来源'}).click();
  await drawer.getByRole('spinbutton',{name:'目标记忆 ID'}).fill('2');
  await drawer.getByRole('button',{name:'预览合并'}).click();
  await expect(drawer).toContainText('原始证据保留');
  await drawer.getByRole('button',{name:'确认这次合并'}).click();
  await expect(drawer.getByRole('button',{name:'撤销此合并'})).toBeVisible();
  await page.reload();
  await drawer.getByRole('button',{name:'撤销此合并'}).click();
  await expect(drawer.getByRole('button',{name:'预览合并'})).toBeVisible();
});
