# F1000_Slack
F the Bot

Schedule a cron job to run once daily at 8 a.m.

Open the crontab `crontab -e` and add a job

```bash
0 8 * * * Rscript /path/to/bot.R
``` 
