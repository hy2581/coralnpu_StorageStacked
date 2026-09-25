// Small trained decoder: all arithmetic executes in the CoralNPU RTL core.
// External weights, tokens, KV cache and observations use the production memory path.
#include <cstdint>
#include <cmath>
#include "llm_config.h"

static volatile float* const weights = reinterpret_cast<volatile float*>(0x90000000u);
static volatile float* const keys = reinterpret_cast<volatile float*>(0x90010000u);
static volatile float* const values = reinterpret_cast<volatile float*>(0x90020000u);
static volatile float* const trace = reinterpret_cast<volatile float*>(0x90030000u);
static volatile uint32_t* const tokens = reinterpret_cast<volatile uint32_t*>(0x90040000u);
static volatile uint32_t* const report = reinterpret_cast<volatile uint32_t*>(0x90050000u);
static uint32_t errors = 0, calls = 0;

static void dump(unsigned pos, unsigned offset, const float* data, unsigned count) {
    for (unsigned i=0; i<count; ++i) trace[pos*LLM_TRACE_STRIDE+offset+i]=data[i];
}
static void norm(const float* x, float* y, unsigned scale, unsigned bias) {
    float mean=0.0f, variance=0.0f;
    for (unsigned i=0;i<LLM_DIM;++i) mean+=x[i];
    mean/=LLM_DIM;
    for (unsigned i=0;i<LLM_DIM;++i) {float z=x[i]-mean;variance+=z*z;}
    const float inv=1.0f/sqrtf(variance/LLM_DIM+1e-5f);
    for (unsigned i=0;i<LLM_DIM;++i) y[i]=(x[i]-mean)*inv*weights[scale+i]+weights[bias+i];
}
static void linear(const float* x, float* y, unsigned rows, unsigned cols, unsigned matrix, int bias=-1) {
    for (unsigned j=0;j<cols;++j) {
        float sum=0.0f;
        for (unsigned i=0;i<rows;++i) sum+=x[i]*weights[matrix+i*cols+j];
        y[j]=sum+(bias>=0?weights[bias+j]:0.0f);
    }
}
static unsigned forward(unsigned pos) {
    ++calls;
    float x[LLM_DIM], n1[LLM_DIM], q[LLM_DIM], k[LLM_DIM], v[LLM_DIM];
    float mixed[LLM_DIM], projected[LLM_DIM], residual[LLM_DIM], n2[LLM_DIM];
    float ff[LLM_HIDDEN], hidden[LLM_DIM], nf[LLM_DIM], logits[LLM_VOCAB];
    float probs[LLM_HEADS*LLM_CONTEXT]={};
    const unsigned id=tokens[pos];
    if(id>=LLM_VOCAB) {++errors;return 0;}
    for(unsigned j=0;j<LLM_DIM;++j) x[j]=weights[W_EMBEDDING+id*LLM_DIM+j]+weights[W_POSITION+pos*LLM_DIM+j];
    dump(pos,T_EMBEDDING,x,LLM_DIM);
    norm(x,n1,W_NORM1_WEIGHT,W_NORM1_BIAS); dump(pos,T_NORM1,n1,LLM_DIM);
    linear(n1,q,LLM_DIM,LLM_DIM,W_Q);linear(n1,k,LLM_DIM,LLM_DIM,W_K);linear(n1,v,LLM_DIM,LLM_DIM,W_V);
    dump(pos,T_Q,q,LLM_DIM);dump(pos,T_K,k,LLM_DIM);dump(pos,T_V,v,LLM_DIM);
    for(unsigned j=0;j<LLM_DIM;++j) {keys[pos*LLM_DIM+j]=k[j];values[pos*LLM_DIM+j]=v[j];}
    constexpr unsigned head_dim=LLM_DIM/LLM_HEADS;
    for(unsigned h=0;h<LLM_HEADS;++h) {
        float peak=-1e30f,total=0.0f;
        for(unsigned t=0;t<=pos;++t) {
            float score=0.0f;
            for(unsigned j=0;j<head_dim;++j) score+=q[h*head_dim+j]*keys[t*LLM_DIM+h*head_dim+j];
            score/=sqrtf(float(head_dim));probs[h*LLM_CONTEXT+t]=score;
            if(score>peak) peak=score;
        }
        for(unsigned t=0;t<=pos;++t) {float p=expf(probs[h*LLM_CONTEXT+t]-peak);probs[h*LLM_CONTEXT+t]=p;total+=p;}
        float check=0.0f;
        for(unsigned t=0;t<=pos;++t) {probs[h*LLM_CONTEXT+t]/=total;check+=probs[h*LLM_CONTEXT+t];}
        if(!(check>0.999f && check<1.001f)) ++errors;
        for(unsigned j=0;j<head_dim;++j) {
            float sum=0.0f;
            for(unsigned t=0;t<=pos;++t) sum+=probs[h*LLM_CONTEXT+t]*values[t*LLM_DIM+h*head_dim+j];
            mixed[h*head_dim+j]=sum;
        }
    }
    dump(pos,T_ATTENTION,probs,LLM_HEADS*LLM_CONTEXT);
    linear(mixed,projected,LLM_DIM,LLM_DIM,W_O);dump(pos,T_ATTENTION_OUTPUT,projected,LLM_DIM);
    for(unsigned j=0;j<LLM_DIM;++j) residual[j]=x[j]+projected[j];
    dump(pos,T_RESIDUAL,residual,LLM_DIM);
    norm(residual,n2,W_NORM2_WEIGHT,W_NORM2_BIAS);dump(pos,T_NORM2,n2,LLM_DIM);
    linear(n2,ff,LLM_DIM,LLM_HIDDEN,W_FF1,W_FF1_BIAS);
    for(unsigned j=0;j<LLM_HIDDEN;++j) if(ff[j]<0.0f) ff[j]=0.0f;
    dump(pos,T_FF,ff,LLM_HIDDEN);
    linear(ff,hidden,LLM_HIDDEN,LLM_DIM,W_FF2,W_FF2_BIAS);
    for(unsigned j=0;j<LLM_DIM;++j) hidden[j]+=residual[j];
    dump(pos,T_HIDDEN,hidden,LLM_DIM);
    norm(hidden,nf,W_NORM_FINAL_WEIGHT,W_NORM_FINAL_BIAS);dump(pos,T_NORM_FINAL,nf,LLM_DIM);
    linear(nf,logits,LLM_DIM,LLM_VOCAB,W_LM_HEAD,W_LM_BIAS);dump(pos,T_LOGITS,logits,LLM_VOCAB);
    unsigned best=0;
    for(unsigned j=0;j<LLM_VOCAB;++j) {
        if(!(logits[j]>-1e10f && logits[j]<1e10f)) ++errors;
        if(logits[j]>logits[best]) best=j;
    }
    return best;
}
int main() {
    report[0]=0x4c4c4d31u;
    for(unsigned i=0;i<LLM_WEIGHT_WORDS;++i) weights[i]=model_image[i];
    for(unsigned i=0;i<LLM_CONTEXT*LLM_DIM;++i) {keys[i]=0.0f;values[i]=0.0f;}
    for(unsigned i=0;i<LLM_PROMPT_LENGTH;++i) tokens[i]=prompt_image[i];
    report[1]=1;
    for(unsigned step=0;step<LLM_GENERATE;++step) {
        report[32+step]=0x70000000u+step;
        const unsigned end=LLM_PROMPT_LENGTH+step;
        unsigned best=0;
        const unsigned begin=LLM_KV_CACHE && step?end-1:0;
        for(unsigned pos=begin;pos<end;++pos) best=forward(pos);
        report[16+step]=best;
        const unsigned returned=report[16+step];
        if(returned!=best) ++errors;
        if(step+1<LLM_GENERATE) tokens[end]=returned;
        report[96+step]=calls;
        report[64+step]=0x71000000u+step;
    }
    report[8]=errors;report[2]=2;
    *reinterpret_cast<volatile uint32_t*>(0xc0000000u)=errors?0xbad00000u:0x600d0000u;
    asm volatile("wfi");
    return 0;
}
