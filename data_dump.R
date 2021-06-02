suppressPackageStartupMessages({
  library(httr)
  library(jsonlite)
  library(purrr)
  library(magrittr)
})

load("state.rdata")

dump <- webhooks %>% pmap(function(...) {
  current <- data.frame(...)
  resp <- GET(
    paste0("https://sciwheel.com/extapi/work/references?projectId=", current$projectId, "&sort=addedDate:desc"),
    add_headers(Authorization = paste("Bearer", f1000auth))
  )
  print(paste(Sys.time(), "channel", current$channel, "GET status", resp$status_code))
  
  
  if (resp$status_code == 200) {
    project.content <- content(resp)
    
    project.notes <- project.content$results %>% map(function(r) {
      noteResp <- GET(
        paste0("https://sciwheel.com/extapi/work/references/", test$id, "/notes?"),
        add_headers(Authorization = paste("Bearer", f1000auth))
      )
    })
    
    names(project.notes) <- project.content$results %>% map_chr(~.x$id)
    list(content = project.content, notes = project.notes)
  } else {
    NULL
  }
})

names(dump) <- webhooks$channel

if(file.exists("data_dump.rds")){
  file.rename("data_dump.rds", "data_dump_old.rds")
}

saveRDS(dump, "data_dump.rds")
