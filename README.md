# F the Bot
A Slack app companion script connecting F1000/Sciwheel to Slack

## Requirements
The bot requires a `state.rdata` object

This object should contain 3 variables:

- `f1000auth` - *character*, a variable containing the F1000/Sciwheel external API access token.
- `lastDate` - *numeric*, a variable containing the last time (in milliseconds since  Jan 1, 1970 00:00:00 UTC) F1000/Sciwheel was queried.
- `webhooks` - *data.frame*, a table with three columns - (*character*) channel, (*numeric*) projectId and (*character*) webhook. Each row contains information about the mapping between the name of the slack channel, the F1000/Sciwheel subproject id from where the papers will be queried and posted to that channel, and finally the complete url of the Slack webhook that the app can use to post to that channel.


## Scheduling

The bot can be scheduled a cron job to run once daily, for example, at 8 a.m.

Open the crontab `crontab -e` and add a job

```bash
0 8 * * * Rscript /path/to/bot.R
```

