/* Static network-none CI helper: local HTTP proxy over Unix socket only. */
#define _POSIX_C_SOURCE 200809L
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>
#define SOCK_PATH "/run/ci-egress/proxy.sock"
#define PROXY_PORT 3128
static void relay_stream(int input,int output) {
    char buffer[32768];
    for(;;){
        ssize_t received=read(input,buffer,sizeof(buffer));
        if(received<=0)break;
        ssize_t used=0;
        while(used<received) {
            ssize_t wrote=write(output,buffer+used,(size_t)(received-used));
            if(wrote<=0)goto end;
            used+=wrote;
        }
    }
end: shutdown(output,SHUT_WR);
}
static void handle(int client,int listener) {
    close(listener);
    int upstream=socket(AF_UNIX,SOCK_STREAM,0);
    if(upstream<0)_exit(120);
    struct sockaddr_un endpoint={0};
    endpoint.sun_family=AF_UNIX;
    strncpy(endpoint.sun_path,SOCK_PATH,sizeof(endpoint.sun_path)-1);
    if(connect(upstream,(struct sockaddr*)&endpoint,sizeof(endpoint))!=0)_exit(121);
    pid_t half=fork();
    if(half<0)_exit(122);
    if(half==0) {relay_stream(client,upstream);close(client);close(upstream);_exit(0);}
    relay_stream(upstream,client);
    close(client);close(upstream);
    (void)waitpid(half,NULL,0);
    _exit(0);
}
static void serve(int syncfd) {
    signal(SIGPIPE,SIG_IGN);
    signal(SIGCHLD,SIG_IGN);
    int fd=socket(AF_INET,SOCK_STREAM,0);
    if(fd<0)_exit(110);
    struct sockaddr_in addr={0};
    addr.sin_family=AF_INET;addr.sin_port=htons(PROXY_PORT);
    addr.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    if(bind(fd,(struct sockaddr*)&addr,sizeof(addr))!=0 || listen(fd,24)!=0)_exit(111);
    if(write(syncfd,"1",1)!=1)_exit(112);
    close(syncfd);
    for(;;) {
        int client=accept(fd,NULL,NULL);
        if(client<0){if(errno==EINTR)continue;break;}
        pid_t child=fork();
        if(child==0){signal(SIGCHLD,SIG_DFL);handle(client,fd);}
        close(client);
    }
    close(fd);_exit(113);
}
int main(int argc,char**argv) {
    if(argc<2)return 101;
    int pipefd[2];if(pipe(pipefd)!=0)return 102;
    pid_t helper=fork();if(helper<0)return 103;
    if(helper==0){close(pipefd[0]);serve(pipefd[1]);}
    close(pipefd[1]);char ready=0;
    ssize_t n=read(pipefd[0],&ready,1);close(pipefd[0]);
    if(n!=1||ready!='1')return 104;
    execvp(argv[1],argv+1);
    perror("CI egress executable");return 105;
}
