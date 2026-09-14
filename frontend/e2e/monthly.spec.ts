import {test,expect} from '@playwright/test';
test('月报呈现月度节奏与自然周交集，返回周报清理旧报告',async({page})=>{
  const summary={id:7,group_id:1,kind:'monthly',period_start:'2024-01-31T16:00:00Z',period_end:'2024-02-29T16:00:00Z',revision:1,status:'PARTIAL'};
  await page.route('**/api/**',async route=>{
    const url=new URL(route.request().url());const p=url.pathname;const json=(body:unknown)=>route.fulfill({contentType:'application/json',body:JSON.stringify(body)});
    if(p==='/api/system/ready')return json({ready:true,checks:{}});
    if(p==='/api/groups')return json([{id:1,display_name:'测试群'}]);
    if(p==='/api/v2/runs')return json({runs:[],total:0});
    if(p==='/api/v2/insights')return json({items:url.searchParams.get('kind')==='monthly'?[summary]:[]});
    if(p==='/api/v2/insights/7')return json({...summary,coverage:{current:{complete:false},previous:{complete:false}},metrics_version:'period-1',
      metrics:{message_count:10,speaker_count:2,comparison:{},members:[],daily_counts:[],champion:null,inactive_previous_members:[],month_days:29,known_daily_average:0.34,
        weekly_trends:[{week_start:'2024-01-29',from:'2024-02-01',through:'2024-02-04',days:4,known_messages:10,complete:false}],keywords:[{word:'显示器',message_count:2}],keyword_basis:'已知消息中的关键词'},sections:[]});
    throw new Error(`Unexpected API ${p}`);
  });
  await page.goto('/#/images?view=monthly&insight=7');
  await expect(page.getByRole('heading',{name:'本月概况 · v1'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'整月节奏'})).toBeVisible();
  await expect(page.getByText('2024-02-01—2024-02-04（4 天）：10 条（已知，覆盖不完整）')).toBeVisible();
  await page.getByRole('tab',{name:'Weekly · 周度洞察'}).click();
  await expect(page.getByRole('heading',{name:'Weekly Insight · 周度变化'})).toBeVisible();
  expect(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('insight')).toBeNull();
});
