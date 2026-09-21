# lan/ — desktop (-> MBP) client templates

`nazerims-mbp:8100` is the auth_proxy on the MacBook Pro; it forwards to the
engine currently loaded (8001 DeepSeek / 8002 Qwen). On the desktop:

```sh
mkdir -p ~/.config/ds4 && scp nazerims-mbp:~/.config/ds4/desktop.key ~/.config/ds4/
# then merge these fragments, replacing REPLACE-WITH-... with: $(cat ~/.config/ds4/desktop.key)
```

Merge commands (run on the desktop):
```sh
jq -s '.[0].providers += .[1].providers' ~/.pi/agent/models.json lan/pi-models.json.ds4lan > /tmp/m && mv /tmp/m ~/.pi/agent/models.json
jq -s '.[0].modelThinkingLevels += .[1].modelThinkingLevels' ~/.pi/agent/settings.json lan/pi-settings.json.ds4lan > /tmp/s && mv /tmp/s ~/.pi/agent/settings.json
jq -s '.[0].provider += .[1].provider' ~/.config/opencode/opencode.json lan/opencode.json.ds4lan > /tmp/o && mv /tmp/o ~/.config/opencode/opencode.json
```
A live auth_proxy auto-follows engine switches on the MBP
(`start`/`restart` = DeepSeek :8001, `start-qwen`/`restart-qwen` = Qwen :8002);
manual `restart-proxy` only needed if the follow-restart printed a warning.
