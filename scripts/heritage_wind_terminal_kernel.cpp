// Sparse synchronous specialization of the V3 thermal clock. Python supplies
// validated, canonical topology and the initial or continued V3 state.
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <vector>

namespace {
constexpr double eps = 1e-10, sigma = 5.670374419e-8, boiling = 373.15;
constexpr double water_cp = 4180.0, latent = 2256400.0;
struct Params {
    const double *area, *capacity, *emiss, *ambient_h, *backing, *alpha, *ignition;
    const double *peak, *growth, *steady, *decay, *heat, *radiative, *fuel_tol;
    const uint8_t *pilot;
};
struct State {
    uint8_t *phase, *valid, *cause;
    double *temp, *water, *fuel, *age, *hrr, *incident, *absorbed, *convection;
    double *released, *ignition, *burnout, *peak_temp;
};
struct Edges {
    const int32_t *offsets, *targets;
    const double *weights;
    const int32_t *contact_offsets, *contact_targets;
    const double *contact_weights;
};
struct Config {
    int32_t n;
    double ambient, heating_delta, max_dt, sampling, gas_temp, contact_h, fraction;
};
struct ContactDemand { int source, target; double fraction, demand; };
struct Scratch {
    double radiation=0., fraction=0., convection=0., applied_fraction=0.;
};
inline bool susceptible(uint8_t s) { return s==1 || s==2; }
inline double integral(double age, double growth, double steady, double decay) {
    if (age < growth) return age*age*age/(3.0*(growth*growth));
    if (age < growth+steady) return growth/3.0+age-growth;
    if (age < growth+steady+decay) {
        double t=age-growth-steady;
        return growth/3.0+steady+t-t*t/(2.0*decay);
    }
    return growth/3.0+steady+decay/2.0;
}
inline double fourth(double t) { double square=t*t; return square*square; }
}

extern "C" int advance(const Config *config, const Params *parameters,
        const State *state, const Edges *edges, double stop, bool stop_when_quiet, bool exact,
        double *time, int64_t *step, double *budget) {
    const auto &c=*config; const auto &p=*parameters; const auto &s=*state; const auto &g=*edges;
    std::vector<int> burns, warm, affected, previous_hrr, newly_burning, next_burns, next_warm;
    std::vector<int64_t> seen(c.n, -1);
    std::vector<uint8_t> tracking(c.n, 0);
    std::vector<Scratch> thermal(c.n);
    std::vector<double> released(c.n), demand(c.n), scale(c.n);
    std::vector<ContactDemand> contact;
    double ambient4 = std::pow(c.ambient, 4.0);
    bool cfl_static = true;
    for (int i=0; i<c.n; ++i) {
        if (s.phase[i]==3) burns.push_back(i);
        if (s.hrr[i]!=0.) previous_hrr.push_back(i);
        if (!susceptible(s.phase[i])) continue;
        if (s.temp[i]>c.ambient) { warm.push_back(i); tracking[i]=1; }
        // All currently cold, untouched receivers stay below max_dt's CFL
        // bound; otherwise this sparse specialization declines the case.
        const double conductance=p.ambient_h[i]+c.contact_h+p.backing[i];
        const double slope=4.0*p.emiss[i]*sigma;
        if (p.capacity[i] < c.max_dt*(conductance+slope*std::pow(c.ambient,3.0))) return 1;
        if (s.temp[i]>=p.ignition[i] || !p.pilot[i] ||
            p.capacity[i] < c.max_dt*(conductance+slope*std::pow(p.ignition[i],3.0))) cfl_static=false;
        if (p.pilot[i] && p.ignition[i]<=c.ambient) return 2;
    }
    long long sample_index=c.sampling>0. ? static_cast<long long>(std::floor(*time/c.sampling))+1 : 0;
    while (*time < stop-eps) {
        if (burns.empty() && stop_when_quiet) return 0;
        double dt=exact ? std::min(c.max_dt,stop-*time) : c.max_dt;
        if (c.sampling>0.) {
            if (sample_index*c.sampling<=*time+eps && std::abs(sample_index*c.sampling-*time)<=eps) ++sample_index;
            dt=std::min(dt,sample_index*c.sampling-*time);
        }
        if (!cfl_static) for (int i:warm) {
            double den=p.ambient_h[i]+c.contact_h+p.backing[i]+4.*p.emiss[i]*sigma*std::pow(s.temp[i],3.);
            if (den>0.) dt=std::min(dt,p.capacity[i]/den);
        }
        for (int i:burns) {
            double residual=p.growth[i]+p.steady[i]+p.decay[i]-s.age[i];
            if (residual>eps) dt=std::min(dt,residual);
        }
        if (!(dt>eps) || !std::isfinite(dt)) return 3;
        const double new_time=*time+dt;
        for (int i:previous_hrr) s.hrr[i]=0.;
        previous_hrr.clear(); affected.clear(); contact.clear();
        double total_release=0., total_rad=0.;
        auto touch=[&](int j) {
            if (seen[j]!=*step) {
                seen[j]=*step; thermal[j]=Scratch{}; affected.push_back(j);
                if (susceptible(s.phase[j]) && !tracking[j]) { warm.push_back(j); tracking[j]=1; }
            }
        };
        // Keep sources in sorted node order to match the source-indexed NumPy
        // irradiance accumulation and the canonical contact graph.
        for (int i:burns) {
            const double g0=p.growth[i], s0=p.steady[i], d0=p.decay[i];
            const double delta=integral(s.age[i]+dt,g0,s0,d0)-integral(s.age[i],g0,s0,d0);
            released[i]=std::min(p.peak[i]*std::max(0.,delta),s.fuel[i]*p.heat[i]);
            const double hrr=released[i]/dt, radiated=hrr*p.radiative[i];
            s.hrr[i]=hrr; previous_hrr.push_back(i); demand[i]=0.; scale[i]=1.;
            total_release+=released[i]; total_rad+=released[i]*p.radiative[i];
            if (radiated>0.) for (int k=g.offsets[i]; k<g.offsets[i+1]; ++k) {
                int j=g.targets[k]; touch(j);
                thermal[j].radiation+=radiated*g.weights[k];
            }
            if (hrr*(1.-p.radiative[i])<=0.) continue;
            const double activity=std::min(1.,hrr/p.peak[i]);
            for (int k=g.contact_offsets[i]; k<g.contact_offsets[i+1]; ++k) {
                int j=g.contact_targets[k];
                if (!susceptible(s.phase[j]) || s.temp[j]>=c.gas_temp) continue;
                touch(j);
                double fraction=g.contact_weights[k]*c.fraction*activity;
                thermal[j].fraction+=fraction;
                contact.push_back({i,j,fraction,0.});
            }
        }
        for (auto &e:contact) {
            e.fraction/=std::max(1.,thermal[e.target].fraction);
            e.demand=e.fraction*p.area[e.target]*c.contact_h*(c.gas_temp-s.temp[e.target]);
            demand[e.source]+=e.demand;
        }
        for (int i:burns) {
            double available=s.hrr[i]*(1.-p.radiative[i]);
            if (demand[i]>available) { scale[i]=available/demand[i]; budget[12]+=1.; }
        }
        for (const auto &e:contact) {
            thermal[e.target].convection+=e.demand*scale[e.source];
            thermal[e.target].applied_fraction+=e.fraction*scale[e.source];
        }
        double intercepted=0., sus_incident=0., absorbed=0., inert=0., inactive=0., conv=0.;
        for (int j:affected) {
            auto &h=thermal[j]; double inc=h.radiation*dt;
            s.incident[j]+=inc; intercepted+=inc;
            s.convection[j]+=h.convection*dt; conv+=h.convection;
            if (susceptible(s.phase[j])) {
                double abs=inc*p.alpha[j]; s.absorbed[j]+=abs; sus_incident+=inc; absorbed+=abs;
            } else if (s.phase[j]==0) { inert+=inc; }
            else if (s.phase[j]==3 || s.phase[j]==4) { inactive+=inc; }
        }
        newly_burning.clear(); next_warm.clear();
        for (int j:warm) {
            Scratch h=seen[j]==*step ? thermal[j] : Scratch{};
            const double diff=s.temp[j]-c.ambient;
            const double energy=(p.alpha[j]*(h.radiation/p.area[j]) + h.convection/p.area[j]
                - p.ambient_h[j]*(1.-h.applied_fraction)*diff
                - p.emiss[j]*sigma*(fourth(s.temp[j])-ambient4)-p.backing[j]*diff)*dt;
            double t=s.temp[j], w=s.water[j]/p.area[j], cap=p.capacity[j];
            if (energy<=0.) {
                t=std::max(c.ambient,t+energy/(cap+w*water_cp));
            } else {
                double available=energy;
                if (w>0. && t<boiling) {
                    double wet_cap=cap+w*water_cp, used=std::min(available,wet_cap*(boiling-t));
                    t+=used/wet_cap; available-=used;
                }
                if (w>0. && t>=boiling-1e-12 && available>0.) {
                    double evaporated=std::min(w,available/latent);
                    w-=evaporated; available-=evaporated*latent; t=boiling;
                }
                if (w<=1e-15 && available>0.) { w=0.; t+=available/cap; }
            }
            s.temp[j]=t; s.water[j]=std::max(0.,w*p.area[j]);
            if (p.pilot[j] && s.fuel[j]>p.fuel_tol[j] && t>=p.ignition[j]) {
                s.phase[j]=3; s.valid[j]=0; s.cause[j]=2; s.ignition[j]=new_time;
                newly_burning.push_back(j); tracking[j]=0;
            } else {
                s.phase[j]=t>c.ambient+c.heating_delta ? 2 : 1;
                s.peak_temp[j]=std::max(s.peak_temp[j],t);
                if (t>c.ambient) next_warm.push_back(j);
                else tracking[j]=0;
            }
        }
        next_burns.clear();
        for (int i:burns) {
            s.released[i]+=released[i];
            s.fuel[i]=std::max(0.,s.fuel[i]-released[i]/p.heat[i]); s.age[i]+=dt;
            bool ended=s.age[i]>=p.growth[i]+p.steady[i]+p.decay[i]-eps;
            if (ended && s.fuel[i]>p.fuel_tol[i]) return 4;
            if (ended || s.fuel[i]<=p.fuel_tol[i]) {
                s.phase[i]=4; s.valid[i]=0; s.fuel[i]=0.; s.burnout[i]=new_time;
            } else next_burns.push_back(i);
        }
        const auto split=next_burns.size();
        std::sort(newly_burning.begin(),newly_burning.end());
        next_burns.insert(next_burns.end(),newly_burning.begin(),newly_burning.end());
        std::inplace_merge(next_burns.begin(),next_burns.begin()+split,next_burns.end());
        burns.swap(next_burns); warm.swap(next_warm);
        budget[0]+=total_release; budget[1]+=total_rad; budget[2]+=total_release-total_rad;
        budget[3]+=intercepted; budget[4]+=std::max(0.,total_rad-intercepted);
        budget[5]+=sus_incident; budget[6]+=absorbed; budget[7]+=sus_incident-absorbed;
        budget[8]+=inert; budget[9]+=inactive; budget[10]+=conv*dt;
        budget[11]+=std::max(0.,total_release-total_rad-conv*dt);
        *time=new_time; ++*step;
    }
    return 0;
}
