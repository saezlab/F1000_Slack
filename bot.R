suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(purrr)
  library(dplyr)
  library(stringr)
  library(rlist)
})

load("state.rdata")

# returns last date
lastDates <- webhooks %>% pmap_dbl(function(...) {
  current <- data.frame(...)

  templastDate <- current$lastDate
  
  resp <- GET(
    paste0("https://sciwheel.com/extapi/work/references?projectId=", current$projectId, "&sort=addedDate:desc"),
    add_headers(Authorization = paste("Bearer", f1000auth))
  )
  print(paste(Sys.time(), "channel", current$channel, "GET status", resp$status_code))

  if (resp$status_code == 200) {
    refs <- content(resp)$results %>% 
      list.filter(f1000AddedDate > templastDate) %>% 
      rev()
    
    blocks <- refs  %>% map(function(r) {
      noteResp <- GET(
        paste0("https://sciwheel.com/extapi/work/references/", r$id, "/notes?"),
        add_headers(Authorization = paste("Bearer", f1000auth))
      )
    
      
      # references to usernames will be matched and converted to lowercase per agreement
      comments <- list.filter(content(noteResp), is.null(highlightText))
      note <- ifelse(noteResp$status_code == 200 & length(comments) > 0,
                     paste0(list.sort(comments, created)[[1]]$comment, "\n"), ""
      ) %>% str_replace_all("@\\w+", tolower)
      

      details <- paste0(
        note,
        r$authorsText, ". <https://sciwheel.com/work/#/items/", r$id, "/detail?collection=", current$projectId, "|", r$title, "> ",
        r$journalName, ". ", r$publishedYear,
        " - added by: ", r$f1000AddedBy,
        ifelse(length(r$f1000Tags) > 0,
          paste0(" - tags: ", paste0(r$f1000Tags, collapse = " ")),
          ""
        )
      )

      if (nchar(details) > 3000) {
        details <- paste0(
          "Full information too long to display. <https://sciwheel.com/work/#/items/",
          r$id, "/detail?collection=", current$projectId, "|", r$title, "> "
        )
      }
      
      list(type = "section", text = list(type = "mrkdwn", text = details))
    })

    if (length(blocks) > 0) {
      webhook <- as.character(current$webhook)
      
      # one paper at a time with wait in between so we don't get rate limited
      alive <- TRUE
      respcodes <- blocks %>% map2_int(refs %>% map(~.x$f1000AddedDate), function(block, addedDate){
        if(alive){
          Sys.sleep(1.05)
          content <- toJSON(list(blocks = list(block)), auto_unbox = TRUE)
          cinoutcome <- POST(url = webhook, content_type_json(), body = content)
          if(outcome$status_code == 200){
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
      })

      print(paste(Sys.time(), "POST status", paste(respcodes, collapse = ", ")))
      
    }
  }
  templastDate
})

if(sum(lastDates > webhooks$lastDate) > 0){
  webhooks$lastDate <- lastDates
  save(f1000auth, webhooks, file = "state.rdata")
}
