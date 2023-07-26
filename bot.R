suppressPackageStartupMessages({
    library(httr)
    library(jsonlite)
    library(purrr)
    library(dplyr)
    library(stringr)
    library(rlist)
    library(slackr)
    library(stringdist)
    library(googledrive)
    library(readr)
})


# get command line secret inputs
args <- commandArgs(trailingOnly = TRUE)

# 1st: credentials for Google Drive
credentials <- args[[1]]

# File path on google drive: 
file_path <- args[[2]]

# Slack bot token:
bottoken <- args[[3]]

# Sciwheel authentication token:
f1000auth <- args[[4]]



# Load the state from Drive
# authenticate to Google drive
drive_auth(path = rawToChar(base64enc::base64decode(credentials)), email = "f1000bot-service-account@f1000bot.iam.gserviceaccount.com" )

# read the file from google drive
webhooks <- drive_read_string(file = file_path) %>% read_csv()


slackr_setup(token = bottoken)
users <- slackr_users()

# returns last date
lastDates <- webhooks %>% pmap_dbl(\(...) {
    current <- data.frame(...)
    
    templastDate <- current$lastDate
    
    resp <- GET(
        paste0(
            "https://sciwheel.com/extapi/work/references?projectId=",
            current$projectId, "&sort=addedDate:desc"
        ),
        add_headers(Authorization = paste("Bearer", f1000auth))
    )
    print(paste(
        Sys.time(), "channel", current$channel, "GET status",
        resp$status_code
    ))
    
    if (resp$status_code == 200) {
        refs <- content(resp)$results %>%
            list.filter(f1000AddedDate > templastDate) %>%
            rev()
        
        blocks <- refs %>% map(\(r) {
            noteResp <- GET(
                paste0("https://sciwheel.com/extapi/work/references/", r$id, "/notes?"),
                add_headers(Authorization = paste("Bearer", f1000auth))
            )
            
            comments <- list.filter(content(noteResp), is.null(highlightText))
            
            note <- ifelse(noteResp$status_code == 200 & length(comments) > 0,
                           paste0(list.sort(comments, created)[[1]]$comment, "\n"), ""
            ) %>% str_replace_all("@\\w+", \(name){
                match <- amatch(
                    str_remove(name, "@") %>%
                        tolower(),
                    users %>%
                        pull(display_name_normalized) %>%
                        str_remove_all("\\s") %>%
                        tolower(),
                    method = "jw",
                    weight = c(d = 0.1, s = 1, i = 1, t = 1),
                    p = 0.1,
                    maxDist = 0.5)
                
                if (is.na(match)) {
                    return(name)
                } else {
                    paste0("<@", users %>% pluck("id", match), ">")
                }
            })
            
            details <- paste0(
                note,
                r$authorsText, ". <", r$fullTextLink, "|", r$title, "> ",
                r$journalName, ". ", r$publishedYear,
                " - added by: ", r$f1000AddedBy,
                ifelse(length(r$f1000Tags) > 0,
                       paste0(" - tags: ", paste0(r$f1000Tags, collapse = " ")),
                       ""
                )
            )
            
            if (nchar(details) > 3000) {
                details <- paste0(
                    "Full information too long to display. ",
                    "<", r$fullTextLink, "|", r$title, "> ",
                    " - added by: ", r$f1000AddedBy
                )
            }
            
            list(type = "section", text = list(type = "mrkdwn", text = details))
        })
        
        if (length(blocks) > 0) {
            webhook <- as.character(current$webhook)
            
            # one paper at a time with wait in between so we don't get rate limited
            alive <- TRUE
            respcodes <- blocks %>% map2_int(
                refs %>% map(~ .x$f1000AddedDate),
                \(block, addedDate) {
                    if (alive) {
                        Sys.sleep(1.05)
                        content <- toJSON(list(blocks = list(block)), auto_unbox = TRUE)
                        outcome <- POST(url = webhook, content_type_json(), body = content)
                        if (outcome$status_code == 200) {
                            if (addedDate > templastDate) {
                                templastDate <<- addedDate
                            }
                        } else {
                            alive <<- FALSE
                        }
                        outcome$status_code
                    } else {
                        -1
                    }
                }
            )
            
            print(paste(Sys.time(), "POST status", paste(respcodes, collapse = ", ")))
        }
    }
    templastDate
})

if (sum(lastDates > webhooks$lastDate) > 0) {
    webhooks$lastDate <- lastDates
    
    # write the file back to google drive
    webhooks %>% write_csv("./temp.csv")
    drive_update(file = file_path, media = "./temp.csv")
}
