# F the Bot
A Slack app companion script connecting F1000/Sciwheel to Slack

## Requirements
The bot requires a `state.rdata` object

This object should contain 3 variables:

- `bottoken` - *character*, a varialb containing the Slack authetincation token for the bot.

- `f1000auth` - *character*, a variable containing the F1000/Sciwheel external API access token.
- `webhooks` - *data.frame*, a table with four columns - (*character*) channel, (*numeric*) projectId, (*character*) webhook and (_numeric_) lastDate. Each row contains information about the mapping between the name of the slack channel, the F1000/Sciwheel subproject id from where the papers will be queried and posted to that channel, the complete url of the Slack webhook that the app can use to post to that channel and the last time  (in milliseconds since  Jan 1, 1970 00:00:00 UTC) F1000/Sciwheel  was queried for that channel/projectId.


## Scheduling

The bot can be scheduled as a cron job to run once daily, for example, at 8 a.m.

Open the crontab `crontab -e` and add a job

```bash
0 8 * * * Rscript /path/to/bot.R
```

