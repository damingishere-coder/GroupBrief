import {test,expect} from '@playwright/test';

for(const width of [1440,390])test(`故事线时间轴与来源 ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});
  const story={id:1,group_id:1,title:'甲的换工作过程',summary:'准备离职到正式入职',version:2};
  await page.route('**/api/**',async route=>{
    const p=new URL(route.request().url()).pathname;const json=(value:unknown)=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
    if(p==='/api/system/ready')return json({ready:true,checks:{}});
    if(p==='/api/groups')return json([{id:1,display_name:'测试群'}]);
    if(p==='/api/v2/storylines')return json({items:[story]});
    if(p==='/api/v2/storylines/1')return json({...story,entries:[{id:9,summary:'收到 Offer',observed_start:'2026-08-18T04:00:00Z',event_at:null,sources:[{claim_key:'offer',message_id:42,quote:'收到 Offer 了'}]}]});
    if(p==='/api/v2/messages/42/context')return json({before:[],message:{id:42,sender_name:'甲',content:'收到 Offer 了',sent_at:'2026-08-18T04:00:00Z'},after:[]});
    if(p==='/api/v2/messages/42/sources')return json({items:[],next_offset:null});
    throw new Error(`Unexpected API ${p}`);
  });
  await page.goto('/#/storylines?storyline=1');
  await expect(page.getByRole('region',{name:'故事线时间轴'})).toContainText('发言观察日期');
  await page.getByRole('button',{name:'查看来源 #42'}).click();
  await expect(page.getByRole('complementary',{name:'来源消息'})).toContainText('收到 Offer 了');
  await page.getByRole('button',{name:'关闭来源'}).click();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
