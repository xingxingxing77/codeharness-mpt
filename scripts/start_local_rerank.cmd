@echo off
rem 本地精排服务（bge-reranker-v2-m3 cross-encoder，记忆腿 rerank 档的端点）
rem 开机自启任务名：CodeharnessLocalRerank；日志：E:\tmp\local_ce_service.log
set PYTHONIOENCODING=utf-8
set PYTHONPATH=E:\Codeharness;E:\Codeharness\logs
set REDIS__DB=15
set LANGFUSE__ENABLED=0
set NO_PROXY=127.0.0.1,localhost,::1
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
cd /d E:\Codeharness
"F:\anaconda\python.exe" -B tests\local_rerank_stub.py --port 9998 --backend ce >> E:\tmp\local_ce_service.log 2>&1
