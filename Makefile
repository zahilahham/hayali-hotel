# Run on the hotel's Linux server from the project folder, after git pull.
# Reception, room, make up and laundry are one server and one build of the pages,
# so every target installs the same release. Do not split it: the door screens
# and the bell talk to that one process.
#
#   make deploy      build the pages, install, restart, check
#   make reception   same, then print the reception link
#   make room        same, then print the guest app link
#   make makeup      same, then print the make up link
#   make laundry     same, then print the laundry link
#   make status      check the running server without installing

PORT ?= 8001
SUDO := $(shell [ "$$(id -u)" -eq 0 ] || echo sudo)
HOST := $(shell hostname -I 2>/dev/null | awk '{print $$1}')
BASE := http://$(or $(HOST),127.0.0.1):$(PORT)

.PHONY: deploy pages install status reception room makeup laundry

deploy: pages install status

pages:
	@command -v npm >/dev/null 2>&1 || { echo "Node.js and npm are needed to build the pages."; exit 1; }
	cd frontend && npm ci && npm run build

install:
	$(SUDO) sh deploy/install.sh

status:
	@i=0; until curl -fsS http://127.0.0.1:$(PORT)/api/health >/dev/null 2>&1; do \
	  i=$$((i+1)); \
	  if [ $$i -ge 20 ]; then \
	    echo "The server is not answering on port $(PORT)."; \
	    echo "Check: systemctl status hayali   and   journalctl -u hayali -n 50"; \
	    exit 1; \
	  fi; \
	  sleep 1; \
	done
	@curl -fsS http://127.0.0.1:$(PORT)/api/health; echo

reception: deploy
	@echo "Reception: $(BASE)/reception"

room: deploy
	@echo "Guest app: $(BASE)/room  (each booking's QR opens $(BASE)/room/<code>)"

makeup: deploy
	@echo "Make up: $(BASE)/makeup"

laundry: deploy
	@echo "Laundry: $(BASE)/laundry"
