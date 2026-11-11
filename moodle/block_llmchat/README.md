# block_llmchat — the course chat in a block

Shows the [local_llmchat](../local_llmchat) course chat in a block (in Boost: the block
drawer), for the conversation that is currently open in that course — the one last used
there in this browser tab, or a new one. There is no conversation list in the block: the ⤢
button in its header opens the same conversation in the full chat page, which lists all
conversations, and the **+** button starts a new one.

The block connects to the chat API only once it is on screen, so a closed drawer costs
nothing. It shows nothing to guests, without `local/llmchat:use`, while the API is not
configured, or on the full chat page itself.

Requires `local_llmchat`. Install to `<moodle>/blocks/llmchat`, then add *Course chat* to a
course (with "Display throughout the course" for its activities too) or to the Dashboard,
where it is the global chat.

```bash
vendor/bin/phpunit --testsuite block_llmchat_testsuite   # inside a Moodle checkout
```
