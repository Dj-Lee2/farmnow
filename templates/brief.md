{{ site.name }} 브리핑 — {{ built }} 기준 (최근 24시간 {{ overnight.total }}건)

■ 오늘의 핵심
{% for it in top5 %}{{ loop.index }}. [{{ it.label }}] {{ it.headline }} ({{ it.source_name }}, {{ it|ftime }})
   {{ it.url }}
{% else %}없음
{% endfor %}
■ 기상특보
{% if warn is none %}확인 불가
{% elif active %}{% for a in active %}- {{ a.kind }}: {{ a.regions }}
{% endfor %}{% else %}- 발효 중인 특보 없음 ({{ warn.as_of|fdt }} 기준)
{% endif %}
{% if tickers.day %}■ 시세 (KAMIS {{ tickers.day }} 조사)
{% for r in tickers.fixed %}- {{ r.disp }} {{ r.cls }} {{ r.price|won }}원/{{ r.unit }} ({{ r.pct|pct }}){% if r.day != tickers.day %} · {{ r.day[5:]|replace('-', '.') }} 조사{% endif %}
{% endfor %}{% for r in tickers.surges %}- [급변] {{ r.disp }} {{ r.cls }} {{ r.price|won }}원/{{ r.unit }} ({{ r.pct|pct }})
{% endfor %}
{% endif %}■ 최근 24시간
{% for it in items if (now - it.event_time).total_seconds() <= 86400 %}- {{ it|ftime }} [{{ it.label }}] {{ it.headline }} ({{ it.source_name }})
{% else %}없음
{% endfor %}
