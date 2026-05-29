"""
检索质量评估脚本
使用 MSMARCO 数据集评估检索效果
评估指标: Hit@5, MRR
"""

from typing import List, Dict, Any
from datasets import load_dataset
from app.rag_milvus import build_rag_components


def hit_at_k(retrieved_ids: List[str], ground_truth_ids: List[str], k: int = 5) -> int:
    """前 K 个结果是否命中正确答案"""
    if not ground_truth_ids:
        return 0
    return int(len(set(retrieved_ids[:k]) & set(ground_truth_ids)) > 0)


def mrr(retrieved_ids: List[str], ground_truth_ids: List[str]) -> float:
    """平均倒数排名"""
    if not ground_truth_ids:
        return 0.0
    for idx, rid in enumerate(retrieved_ids):
        if rid in ground_truth_ids:
            return 1.0 / (idx + 1)
    return 0.0


def calculate_recall(retrieved_ids: List[str], ground_truth_ids: List[str], k: int = 5) -> float:
    """Recall@K: 检索到的相关文档占所有相关文档的比例"""
    if not ground_truth_ids:
        return 0.0
    retrieved_relevant = len(set(retrieved_ids[:k]) & set(ground_truth_ids))
    total_relevant = len(set(ground_truth_ids))
    return retrieved_relevant / total_relevant if total_relevant > 0 else 0.0


def evaluate_retrieval(num_samples: int = 50) -> Dict[str, Any]:
    """
    使用 MSMARCO 数据集评估检索质量

    Args:
        num_samples: 评估样本数量，默认 50 条

    Returns:
        评估结果字典
    """
    print("=" * 60)
    print("开始检索质量评估")
    print("=" * 60)

    # 加载 MSMARCO 数据集（从本地文件）
    print(f"\n[1/4] 加载 MSMARCO 数据集 (样本数: {num_samples})...")
    try:
        import os
        local_data_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "eval", "datasets", "ms_marco_v2_1"
        )
        msmarco = load_dataset(
            "parquet",
            data_files=os.path.join(local_data_dir, "validation-00000-of-00001.parquet"),
            split="train"
        )
        print(f"✅ 数据集加载成功，共 {len(msmarco)} 条样本")
    except Exception as e:
        print(f"❌ 数据集加载失败: {e}")
        return {"error": str(e)}

    # 初始化 RAG 组件
    print("\n[2/4] 初始化 RAG 组件...")
    try:
        index, retriever, reranker, query_engine = build_rag_components()
        print("✅ RAG 组件初始化成功")
    except Exception as e:
        print(f"❌ RAG 组件初始化失败: {e}")
        return {"error": str(e)}

    # 执行检索评估
    print(f"\n[3/4] 开始评估 {num_samples} 条样本...")
    hit_scores = []
    mrr_scores = []
    recall_scores = []
    results_detail = []

    for i, sample in enumerate(msmarco.select(range(num_samples))):
        if (i + 1) % 10 == 0:
            print(f"  进度: {i + 1}/{num_samples}")

        question = sample["query"]

        # 获取相关文档的索引作为 ground truth
        relevant_indices = [
            j for j, sel in enumerate(sample["passages"]["is_selected"])
            if sel == 1
        ]

        try:
            # 执行检索
            response = query_engine.query(question)
            retrieved_ids = [n.node_id for n in response.source_nodes]

            # 计算指标
            hit = hit_at_k(retrieved_ids, [str(idx) for idx in relevant_indices], k=5)
            mrr_score = mrr(retrieved_ids, [str(idx) for idx in relevant_indices])
            recall = calculate_recall(retrieved_ids, [str(idx) for idx in relevant_indices], k=5)

            hit_scores.append(hit)
            mrr_scores.append(mrr_score)
            recall_scores.append(recall)

            results_detail.append({
                "question": question,
                "hit@5": hit,
                "mrr": mrr_score,
                "recall@5": recall,
            })
        except Exception as e:
            print(f"  ⚠️ 样本 {i + 1} 处理失败: {e}")
            hit_scores.append(0)
            mrr_scores.append(0.0)
            recall_scores.append(0.0)

    # 汇总结果
    print("\n[4/4] 生成评估报告...")
    avg_hit5 = sum(hit_scores) / len(hit_scores) if hit_scores else 0
    avg_mrr = sum(mrr_scores) / len(mrr_scores) if mrr_scores else 0
    avg_recall5 = sum(recall_scores) / len(recall_scores) if recall_scores else 0

    result = {
        "dataset": "MSMARCO v2.1",
        "sample_count": num_samples,
        "successful_count": len(hit_scores),
        "hit@5": round(avg_hit5, 4),
        "mrr": round(avg_mrr, 4),
        "recall@5": round(avg_recall5, 4),
        "details": results_detail,
    }

    # 打印摘要
    print("\n" + "=" * 60)
    print("检索质量评估结果")
    print("=" * 60)
    print(f"数据集: MSMARCO v2.1")
    print(f"样本数: {num_samples}")
    print(f"成功评估: {len(hit_scores)}")
    print("-" * 60)
    print(f"Hit@5:     {avg_hit5:.2%}")
    print(f"MRR:       {avg_mrr:.2%}")
    print(f"Recall@5:  {avg_recall5:.2%}")
    print("=" * 60)

    return result


def generate_retrieval_report(result: Dict[str, Any]) -> str:
    """生成 Markdown 格式的检索评估报告"""
    if "error" in result:
        return f"# 检索评估报告\n\n**错误**: {result['error']}"

    report = f"""# 检索质量评估报告

**评估时间**: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
**数据集**: {result['dataset']}
**样本数**: {result['sample_count']}

## 核心指标

| 指标 | 值 | 说明 |
|------|-----|------|
| **Hit@5** | {result['hit@5']:.2%} | 前 5 个结果中包含正确答案的比例 |
| **MRR** | {result['mrr']:.2%} | 正确答案的平均倒数排名 |
| **Recall@5** | {result['recall@5']:.2%} | 检索到的相关文档占所有相关文档的比例 |

## 评估详情

共评估 {result['successful_count']} 条样本。

"""
    return report


if __name__ == "__main__":
    result = evaluate_retrieval(num_samples=50)

    # 生成报告
    report = generate_retrieval_report(result)

    # 保存报告
    with open("app/eval/retrieval_eval_report.md", "w", encoding="utf-8") as f:
        f.write(report)

    print("\n📄 报告已保存到 app/eval/retrieval_eval_report.md")