#define _GNU_SOURCE
#include <sys/prctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct { unsigned long long rchar,wchar,syscr,syscw,read_bytes,write_bytes,cancelled; } Counts;
static int counters(Counts *out) {
    char buffer[2048]={0},key[64]; unsigned long long value;
    int fd=open("/proc/self/io",O_RDONLY); if(fd<0)return -1;
    ssize_t size=read(fd,buffer,sizeof(buffer)-1);close(fd);if(size<=0)return -1;
    for(char *line=strtok(buffer,"\n");line;line=strtok(NULL,"\n")) {
        if(sscanf(line,"%63[^:]: %llu",key,&value)!=2)continue;
        if(!strcmp(key,"rchar"))out->rchar=value;
        else if(!strcmp(key,"wchar"))out->wchar=value;
        else if(!strcmp(key,"syscr"))out->syscr=value;
        else if(!strcmp(key,"syscw"))out->syscw=value;
        else if(!strcmp(key,"read_bytes"))out->read_bytes=value;
        else if(!strcmp(key,"write_bytes"))out->write_bytes=value;
        else if(!strcmp(key,"cancelled_write_bytes"))out->cancelled=value;
    }
    return 0;
}
int main(int argc,char **argv) {
    if(argc<2)return 125;
    if(prctl(PR_SET_CHILD_SUBREAPER,1)!=0)return 125;
    Counts before={0},after={0};if(counters(&before))return 125;
    pid_t child=fork();if(child<0)return 125;
    if(child==0){execvp(argv[1],argv+1);perror("sentinel exec");_exit(127);}
    int primary=125,status;unsigned reaped=0;pid_t pid;
    for(;;){
        pid=waitpid(-1,&status,0);
        if(pid<0){if(errno==EINTR)continue;if(errno==ECHILD)break;return 125;}
        ++reaped;
        if(pid==child)primary=WIFEXITED(status)?WEXITSTATUS(status):128+WTERMSIG(status);
    }
    if(counters(&after))return 125;
    printf("\nSPINFLOW_TREE_IO {\"rchar\":%llu,\"wchar\":%llu,\"syscr\":%llu,\"syscw\":%llu,\"read_bytes\":%llu,\"write_bytes\":%llu,\"cancelled_write_bytes\":%llu,\"direct_or_adopted_children_reaped\":%u,\"command_returncode\":%d}\n",
        after.rchar-before.rchar,after.wchar-before.wchar,after.syscr-before.syscr,after.syscw-before.syscw,
        after.read_bytes-before.read_bytes,after.write_bytes-before.write_bytes,after.cancelled-before.cancelled,reaped,primary);
    return primary;
}
