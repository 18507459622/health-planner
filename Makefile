# health-planner dev commands
#
# Windows needs make (Git Bash / WSL / scoop install make all work).
# Without make, just copy any command below into PowerShell.

VENV := backend/.venv
ifeq ($(OS),Windows_NT)
PY := $(CURDIR)/$(VENV)/Scripts/python.exe
else
PY := $(CURDIR)/$(VENV)/bin/python
endif

.PHONY: help install install-dev test test-verbose test-gaps run clean \
        redis-up redis-down redis-status

# 国内直连 Docker Hub 通常超时，默认走 daocloud 镜像
REDIS_IMAGE ?= docker.m.daocloud.io/library/redis:7-alpine
REDIS_NAME  ?= health-planner-redis

help:
	@echo "make install       install runtime deps"
	@echo "make install-dev   install runtime + test deps"
	@echo "make test          run all tests"
	@echo "make test-gaps     list known-gap cases (= the fix backlog)"
	@echo "make run           start backend"
	@echo "make redis-up      start persistent Redis (AOF + named volume)"
	@echo "make redis-down    stop and remove the container (volume is kept)"
	@echo "make redis-status  show container status, keys and TTLs"

install:
	$(PY) -m pip install -r backend/requirements.txt

install-dev:
	$(PY) -m pip install -r backend/requirements-dev.txt

test:
	$(PY) -m pytest backend

test-verbose:
	$(PY) -m pytest backend -v

# Known-gap backlog: these cases assert CURRENT behaviour.
# Every time one gets fixed, drop its @pytest.mark.gap.
test-gaps:
	$(PY) -m pytest backend -m gap -v

run:
	cd backend && $(PY) -m uvicorn server:app --reload

# 真 Redis：不接的话记忆只活在进程内存里，服务一重启画像和会话就没了。
# --appendonly yes 开 AOF；命名卷让数据在容器重建后仍在。
redis-up:
	@docker image inspect redis:7-alpine >/dev/null 2>&1 || ( \
		echo "pulling $(REDIS_IMAGE) ..."; \
		docker pull $(REDIS_IMAGE) && docker tag $(REDIS_IMAGE) redis:7-alpine )
	@docker rm -f $(REDIS_NAME) >/dev/null 2>&1 || true
	docker run -d --name $(REDIS_NAME) --restart unless-stopped \
		-p 6379:6379 -v hp-redis-data:/data \
		redis:7-alpine redis-server --appendonly yes
	@echo ""
	@echo "Redis started. Remember to add this line to backend/.env:"
	@echo "    REDIS_URL=redis://localhost:6379/0"
	@docker exec $(REDIS_NAME) redis-cli ping

redis-down:
	-docker rm -f $(REDIS_NAME)
	@echo "Container removed; data is still in the named volume hp-redis-data."
	@echo "To wipe it completely: docker volume rm hp-redis-data"

redis-status:
	@docker ps -a --filter name=$(REDIS_NAME) --format '  {{.Names}}  {{.Status}}  {{.Ports}}'
	@docker exec $(REDIS_NAME) redis-cli --scan 2>/dev/null | while read k; do \
		printf "    %-44s TTL=%s\n" "$$k" "$$(docker exec $(REDIS_NAME) redis-cli ttl "$$k")"; \
	done || true

clean:
	-find . -type d -name __pycache__ -prune -exec rm -rf {} +
	-find . -type d -name .pytest_cache -prune -exec rm -rf {} +
