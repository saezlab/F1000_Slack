## ---------------------------
## Author: Jan D. Lanzer
##
## Date Created: 2021-12-09
##
## Copyright (c) Jan D. Lanzer, 2021
## Email: Jan.Lanzer@bioquant.uni-heidelberg.de
##
## Purpose of script:
##
##  process that data dump of SciWheel statistics for plotting
## 
## Main steps: 
## 1) Subset data dump to the last year (optional)
## 2) the listed data structure is transformed to tidy table format for 
##    i) paper counts, ii) notes counts and iii) tags counts.
## 3) simple bar plots are generated and saved at the end


library(tidyverse)

data= readRDS("Sciwheel_update/data_dump.rds")

# data object explanation 
# for paper info = data-> list of clubs -> contents -> list of papers ->  list of features
# for note info = data-> list of clubs -> notes -> list of papers -> list of notes -> list of features
# note: club refers to SciWheel folder (and slack channel), not necessary a club


# data prep -----------------------------------------------------------------------------------

# prepare data:
clubs= names(data)

# rename object 
all_paper_ids= map(clubs, function(x){
  map(data[[x]]$content, function(y){
    return(y$id)
    
    
  })%>% unlist()
})
names(all_paper_ids)= clubs
for(i in clubs){
  names(data[[i]]$content)= all_paper_ids[[i]]
}

#subset data to one year ----------------------------------------------------------------------------------

# translate milisec to year. 
mil_to_year= function(mil){
  mil/1000/60/60/24/365
}

# translate year to millisec
year_to_mil = function(year){
  mil= year*1000*60*60*24*365
}

# subset function,
# will remove papers from the data object that have not been posted within the 
# last year ( defined by the timepoint of most recent paper minus nyear)
subset_data_to_last_year= function(data, n_years= 1){
  per.user= map(clubs, function(x){
    map(data[[x]]$content, function(y){
      y$f1000AddedDate
    })%>% unlist()
  })
  
  names(per.user)= clubs
  
  #transform to table format:
  per.user.df= lapply(names(per.user),function(x) (enframe(per.user[[x]]) %>% mutate(folder = x)))%>% 
    do.call(rbind,. )
  
  ## Define here the interval (in milli secs) that youre intersted in 
  # we will select most recent paper and subtract a year 
  # calculate the most recent paper minus the interval of interest:
  mil_sec_cut_high= max(per.user.df$value)
  mil_sec_cut_low= max(per.user.df$value)- year_to_mil(n_years)
  
  ## now that we have the interval subset the data object
  
  # get paper ids of paper in interval
  paper_ids= map(clubs, function(x){
    map(data[[x]]$content, function(y){
      if(y$f1000AddedDate<= mil_sec_cut_high & 
         y$f1000AddedDate>mil_sec_cut_low ){
        return(y$id)}
      return(NULL)
      
    })%>% unlist()
  })
  
  names(paper_ids)= clubs
  
  #subset obj
  data_sub=data
  
  ## subset the content
  for(i in names(data_sub)){
    #print(i)
    for(j in names(data_sub[[i]]$content)){
      #print(j)
      if(!j %in% unlist(paper_ids)){
        data_sub[[i]]$content= data_sub[[i]]$content[names(data_sub[[i]]$content)!= j]
      }
    }
  }
  
  ##subset the notes:
  
  for(i in names(data_sub)){
    #print(i)
    for(j in names(data_sub[[i]]$notes)){
      #print(j)
      if(!j %in% unlist(paper_ids)){
        data_sub[[i]]$notes= data_sub[[i]]$notes[names(data_sub[[i]]$notes)!= j]
      }
    }
  }
  
  return(data_sub)
}

# run this if you want to subset the data to the last year:
data= subset_data_to_last_year(data, n_years = 1)

# number of papers ---------------------------------------------------

#### paper per club:
club.papers= map(clubs, function(x){
  length(data[[x]]$content)
}) %>% unlist()

names(club.papers)= clubs

#number of papers per club:
club.papers

#total papers posted:
total.papers= sum(club.papers)


#### paper per user
per.user = 
  map(clubs, function(x){
    map(data[[x]]$content, function(y){
      y$f1000AddedBy
    })%>% unlist()%>% table
  })

names(per.user)= clubs

#transform to table format:
per.user.df= lapply(names(per.user),function(x) (enframe(per.user[[x]]) %>% mutate(folder = x)))%>% 
  do.call(rbind,. )%>% mutate(value= as.integer(value))

#simple plot
ggplot(per.user.df, aes(x= name, y= folder, fill = value))+
  geom_tile() + theme(axis.text.x = element_text(angle = 45, vjust = 1, hjust=1))


# Notes  --------------------------------------------------------------------------------
# there are multiple notes per paper, also text highlights are saved as notes.
# this will get per club per paper per user the sum of lengths of all notes

#### note length
notes.per.user = 
  map(clubs, function(club){
    map(data[[club]]$notes, function(paper){
      notelengths= map(paper, function(notes){
        note.l= nchar(notes$comment)
        if(note.l<0){return(NULL)}
        names(note.l)= notes$user
        note.l
        })%>% unlist()
      # mutliple users per paper commented,summ up per user the note length
      if(is.null(notelengths)){return(tibble(name= "nobody", "value"= 0))}
      summed= 
        map(unique(names(notelengths)), function(user){
        sum(notelengths[user])
      })
      names(summed)= unique(names(notelengths))
      enframe(summed)%>% mutate(value=unlist(value))
       # sum the length of all comments made on a paper
    }) %>% do.call(rbind, .)
  })

names(notes.per.user)= clubs

# bring in tidy format:
notes.per.user.df= lapply(names(notes.per.user),function(x){
  if(!is.null(dim(notes.per.user[[x]])[1])){
    notes.per.user[[x]] %>% mutate(folder = x)}
} 
)%>% do.call(rbind, .)

# this df now contains for each paper a row, telling us who commented how many characters and from which folder was this paper

## plot number of characters per paper per user
notes.per.user.df %>%
  group_by(name) %>% 
    ggplot(., aes(x= name,y= value ))+
  geom_boxplot()+
  geom_jitter(alpha= 0.3)+
  coord_flip()

# plot number of notes per user:
notes.per.user.df %>%
  group_by(name) %>% 
  count %>% 
  ggplot(., aes(x= name,y= n))+
  geom_col()+
  coord_flip()



# Tags ----------------------------------------------------------------------------------------

#### tags per club
tags.per.club = 
  map(clubs, function(x){
    map(data[[x]]$content, function(y){
      y$f1000Tags
    })%>% unlist()%>% table
  })

names(tags.per.club)= clubs

tags.per.club.df= lapply(names(tags.per.club),function(x) (enframe(tags.per.club[[x]]) %>% mutate(folder = x)))%>% 
  do.call(rbind,. )%>% mutate(value= as.integer(value))      

#simple plot
ggplot(tags.per.club.df, aes(x= name, y= folder, fill = value))+
  geom_tile() + theme(axis.text.x = element_text(angle = 45, vjust = 1, hjust=1))

#### tags per user
  tags.per.user = 
  map(clubs, function(x){
    map(data[[x]]$content, function(y){
      if(length(y$f1000Tags)==0){
        t= "noTag"
        names(t)= y$f1000AddedBy
        t= enframe(t)%>% mutate(value= unlist(value))
        return(t)
      }
      t= y$f1000Tags
      u= y$f1000AddedBy
      names(t)= rep(u, length(t))
      enframe(t)%>% mutate(value= unlist(value))
    })
  })

names(tags.per.user)= clubs

tags.per.user.df= lapply(names(tags.per.user),function(x) (do.call(rbind,tags.per.user[[x]]))) %>% 
  do.call(rbind,. )%>% group_by(name, value)%>%count

#simple plot
ggplot(tags.per.user.df, aes(x= name, y= value, fill = n))+
  geom_tile() + theme(axis.text.x = element_text(angle = 45, vjust = 1, hjust=1))





# nice plots ----------------------------------------------------------------------------------
# we will generate a serious of informative overview plots

unify_axis= function(p){
  p+theme(axis.text = element_text(size=12, color ="black"),
          axis.title = element_blank())
    
}



#### number of papers by user and folder

p1= per.user.df%>% 
  group_by(name)%>%
  summarize(value2= sum(value))%>%
  ggplot(., aes(x= reorder(name, value2), y = value2))+
  geom_col()+
  coord_flip()+
  theme_bw()+
  ggtitle(paste0("paper per user (total:" , total.papers, ")"))

unify_axis(p1)

#papers per club

p2= per.user.df%>% 
  group_by(folder)%>%
  summarize(value2= sum(value))%>%
  ggplot(., aes(x= reorder(folder, value2), y = value2))+
  geom_col()+
  coord_flip()+
  theme_bw()+
  ggtitle(paste0("paper per club (total:" , total.papers, ")"))

unify_axis(p2)

#### tags usage

#tag frequency
p3= tags.per.user.df %>%
  group_by(value)%>% 
  summarize(n2= sum(n))%>%
  ggplot(., aes(x= reorder(value, n2), y = n2))+
  geom_col()+
  coord_flip()+
  theme_bw()+
  ggtitle(paste0("paper per tag (total:" , total.papers, ")"))
unify_axis(p3)  

### commenting papers

notes.per.user.df

p4= tags.per.user.df %>%
  group_by(name)%>% 
  summarize(n2= sum(n))%>%
  ggplot(., aes(x= reorder(value, n2), y = n2))+
  geom_col()+
  coord_flip()+
  theme_bw()+
  ggtitle(paste0("paper per user (total:" , total.papers, ")"))
unify_axis(p3)  


pdf("overviewplots2022.pdf", 
    width= 6, 
    height= 5.5)
unify_axis(p1)  
unify_axis(p2)  
unify_axis(p3)
dev.off()

