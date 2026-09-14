"""Calendar-month rhythms and conservatively bounded topic lifecycle evidence."""
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta
from zoneinfo import ZoneInfo

from app.knowledge.search_text import normalize,units

STOP={'今天','这个','那个','什么','就是','还是','但是','然后','我们','你们','他们','没有','可以','一个','不是','感觉','真的','哈哈','这样','现在','时候','自己','已经','大家','怎么','如果','所以','因为','你说','我说','的事','the','and','http','https','com','www','图片','视频','表情','消息'}


def metrics(current,rows,start,end,tz):
    start_day=datetime.fromisoformat(start).astimezone(ZoneInfo(tz)).date()
    end_day=datetime.fromisoformat(end).astimezone(ZoneInfo(tz)).date()
    weeks=defaultdict(list)
    for item in current['daily_counts']:
        day=date.fromisoformat(item['date']);monday=day-timedelta(days=day.weekday())
        weeks[monday.isoformat()].append(item)
    current['month_days']=(end_day-start_day).days
    current['known_daily_average']=round(current['message_count']/current['month_days'],2)
    current['weekly_trends']=[{'week_start':week,'from':items[0]['date'],'through':items[-1]['date'],
                               'days':len(items),'known_messages':sum(i['count'] or 0 for i in items),
                               'complete':all(i['complete'] for i in items)} for week,items in sorted(weeks.items())]
    # Counts are distinct messages containing a token, not claims of semantic topics.
    counter=Counter()
    for row in rows:
        words=set()
        for kind,value in units(normalize(row['content'])[0]):
            if kind=='c':words.update(value[i:i+2] for i in range(len(value)-1))
            elif len(value)>=3 and not value.isdigit():words.add(value)
        counter.update(word for word in words if word not in STOP)
    current['keywords']=[{'word':word,'message_count':count} for word,count in sorted(counter.items(),key=lambda pair:(-pair[1],pair[0]))[:20] if count>=2]
    current['keyword_basis']='本地字词匹配的已知消息数，过滤通用词；不等于语义话题热度。'
    return current
