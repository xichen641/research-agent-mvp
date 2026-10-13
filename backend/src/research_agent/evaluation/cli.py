import argparse
from collections.abc import Sequence
from pathlib import Path

from research_agent.evaluation.dataset import load_retrieval_dataset
from research_agent.evaluation.retrieval_eval import evaluate_retrieval


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-agent-eval")
    commands = parser.add_subparsers(dest="command", required=True)
    retrieval = commands.add_parser("retrieval")
    retrieval.add_argument("--dataset", type=Path, required=True)
    arguments = parser.parse_args(argv)

    if arguments.command == "retrieval":
        metrics = evaluate_retrieval(load_retrieval_dataset(arguments.dataset))
        print(f"Cases: {metrics.case_count}")
        print(f"Recall@5: {metrics.recall_at_5:.4f}")
        print(f"Recall@10: {metrics.recall_at_10:.4f}")
        print(f"MRR: {metrics.mean_reciprocal_rank:.4f}")
        return 0
    raise AssertionError("unreachable command")


if __name__ == "__main__":
    raise SystemExit(main())
