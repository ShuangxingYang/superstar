"""
probe_reasoning.py —— 一次性探针:看当前配置的 LLM 流式响应里,除了 content,
到底吐不吐「思考过程」字段、字段叫什么。

背景:推理模型(gpt-5 系、deepseek-reasoner 等)首字前会思考,思考内容不进 delta.content,
被 loop._accumulate 丢弃 → 体感"憋一会整段吐"(详见记忆 superstar-reasoning-model-stream-shape)。
要在页面显示思考过程,得先拿真实数据确认:网关到底给不给这个字段、字段名是什么。
不猜模型名——DeepSeek 系叫 reasoning_content,OpenAI o/gpt-5 系叫 reasoning,
tokenhub 网关还可能改名。所以用 model_dump() 把 delta 全字段打出来,眼见为实。

还会分别用几种「请求参数形状」试探——有些网关默认不开思考,要显式传 reasoning_effort;
有些吃 extra_body。哪种形状能把思考字段逼出来,就用哪种。

跑法:cd backend && uv run python scripts/probe_reasoning.py
     想探别的模型,先在设置页/config.json 把 llm 换过去再跑。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 让单跑脚本能 import app(backend 入 path)

from app.services import llm

PROMPT = "3个连续奇数的和是99,这三个数分别是多少?先想清楚推理过程,再给出答案。"

# 几种请求形状:名字 -> 传给 create() 的额外 kwargs。
# 逐个试,看哪种能让网关吐出思考字段(reasoning / reasoning_content)。
VARIANTS: dict[str, dict] = {
    "baseline(不带思考参数)": {},
    "reasoning_effort=high(顶层)": {"reasoning_effort": "high"},
    "extra_body.reasoning_effort=high": {"extra_body": {"reasoning_effort": "high"}},
    "extra_body.reasoning={effort:high}": {"extra_body": {"reasoning": {"effort": "high"}}},
}


def probe_once(client, model, label, extra) -> bool:
    """跑一种参数形状,打印 delta 全字段快照 + 统计。返回是否见到思考字段。"""
    print(f"\n===== {label} =====")
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            stream=True,
            **extra,
        )
    except Exception as e:  # noqa: BLE001 - 网关不认这个参数就跳过,试下一种
        print(f"  ✗ 请求被拒: {type(e).__name__}: {e}")
        return False

    seen_fields: dict[str, int] = {}   # 非 content 的非空字段 -> 出现的 chunk 数
    content_chars = 0
    reasoning_chars = 0
    first_dump = None                  # 首个非空 delta 的全字段快照
    try:
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            dumped = delta.model_dump()  # pydantic → dict,含厂商扩展字段(extra='allow')
            if first_dump is None and any(v for k, v in dumped.items() if k != "role"):
                first_dump = dumped
            for k, v in dumped.items():
                if k == "content":
                    content_chars += len(v or "")
                elif v and k != "role":
                    seen_fields[k] = seen_fields.get(k, 0) + 1
                    if k in ("reasoning_content", "reasoning"):
                        reasoning_chars += len(v if isinstance(v, str) else str(v))
    except Exception as e:  # noqa: BLE001 - 流中途崩就记一笔,别影响别的形状
        print(f"  ✗ 流式读取中断: {type(e).__name__}: {e}")
        return False

    print("  首个非空 delta 全字段:", json.dumps(first_dump, ensure_ascii=False))
    print(f"  非 content 非空字段: {seen_fields or '(无)'}")
    print(f"  content={content_chars} 字  reasoning={reasoning_chars} 字")
    return reasoning_chars > 0


if __name__ == "__main__":
    client, model = llm.get_llm_client()
    print(f"探针模型: {model}")

    hit = None
    for label, extra in VARIANTS.items():
        if probe_once(client, model, label, extra):
            hit = label
            break   # 逼出来一种就够了

    print("\n" + "=" * 40)
    if hit:
        print(f"✅ 有戏:「{hit}」这种形状下,网关会吐思考字段,可做思考过程展示。")
    else:
        print("❌ 各形状都没见到思考字段——此模型/网关不透传思考轨迹,展示功能拿不到数据。")
