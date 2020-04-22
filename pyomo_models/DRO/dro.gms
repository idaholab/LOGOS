$inlinecom { }               
*-------------- GAMS AND DOLLAR CONTROL OPTIONS -----------------------
$OFFUPPER OFFSYMLIST OFFSYMXREF
OPTIONS
   LP=cplex, MIP=cplex,
   LIMCOL = 0,     LIMROW  =100,       SOLPRINT = OFF,   DECIMALS = 3
   RESLIM = 120000,  ITERLIM = 500000, SEED   = 3141
   optca=0.0000001, optcr=0.0000001;

SETS 
  i /i1*i16/  {candidate projects: UPDATE}
  j /A,B,C/   {options: UPDATE}
  t /t1*t5/   {time periods: UPDATE}
  w /w1*w10/  {scenarios: UPDATE}
;

SET
  m(i) /i2,i5,i7,i10,i12,i14 /  {must-do set: UPDATE}
;

ALIAS(i,ip);  {ip stands for i-prime}
ALIAS(i,ipp); 
ALIAS(j,jp);
ALIAS(w,wp);

{NOTE: current formulation hardwired so that budget uncertainty is only in w}

PARAMETERS
 q(w)         {probability mass function} 
/
$include pmf.txt
/;
display q;
SCALAR qsum;
qsum=sum(w,q(w));
display qsum;
ABORT$(ABS(qsum-1.0) GT 0.000001)"pmf does not sum to unity";

TABLE 
 b(t,w)       {budget in year t under scenario w} 
$include budget.txt
;
display b;

PARAMETER 
  epsradius
;
epsradius=0.1;
PARAMETER
  dist(w,wp)
;
dist(w,wp)=sum(t,(b(t,w)-b(t,wp))*(b(t,w)-b(t,wp)));
dist(w,wp)=sqrt(dist(w,wp));


TABLE
 a(i,j)         {NPV of project i}
$include npv16.txt
;
display a;


TABLE c(i,j,t)       {cost of project i in period t}
$include cost16.txt
;
display c;

SET ijok(i,j);
ijok(i,j)=no;
ijok(i,j)$(a(i,j) ne NA)=yes;
display ijok;

BINARY VARIABLES
 x(i,j,w)       {1 if project i selected under option j for scenario w; 0 otherwise}
 y(i,w)         {1 if project i is selected under scenario w for some option}
 s(i,ip)      {1 if i has priority at least as high as ip}
 z(i,j)       {1 if (i,j) is used under some w}
;
POSITIVE VARIABLES {the gamma variable in DRO}
 gamma
;
VARIABLE {the nu variable in DRO}
 nu(w)
;
VARIABLE
 obj            {objective function value}

EQUATIONS
 objective
 whosehigher(i,ip)
 precedence(i,ip,w)
 budget(t,w)
 requiresome(i,w)
 definey(i,w)
 consistent(i,ip,j,w)
* yousuck(w)
  definez(i,j)
  atmostonej(i)
  whoselower(i,ip)
  triple(i,ip,ipp)
  drocon(w,wp)
;

objective.. obj =E= -gamma*epsradius + sum(w, q(w)*nu(w)) ;

drocon(w,wp).. -gamma*dist(w,wp) + nu(w) =L= sum((i,j)$ijok(i,j), a(i,j)*x(i,j,wp)) ;

whosehigher(i,ip)$(ord(i) LT ord(ip)).. s(i,ip) + s(ip,i) =G= 1 ;
whoselower(i,ip)$(ord(i) LT ord(ip)).. s(i,ip) + s(ip,i) =L= 1 ;
triple(i,ip,ipp)$((ord(i) NE ord(ip)) AND (ord(ip) NE ord(ipp))).. s(i,ip) + s(ip,ipp) + s(ipp,i) =L= 2 ;


precedence(i,ip,w)$(ord(i) NE ord(ip)).. y(i,w) =G= y(ip,w) + s(i,ip) - 1 ;

budget(t,w).. sum((i,j)$ijok(i,j),c(i,j,t)*x(i,j,w)) =L= b(t,w)  ;

requiresome(i,w)$m(i).. y(i,w) =E= 1;

definey(i,w).. sum(j$ijok(i,j),x(i,j,w)) =E= y(i,w);

consistent(i,ip,j,w)$(ijok(ip,j) AND (ord(i) ne ord(ip))).. x(ip,j,w) + s(i,ip) - 1 =L= sum(jp$(ijok(i,jp) AND ord(jp) le ord(j)),x(i,jp,w));

*yousuck(w)$(ord(w) lt card(w)).. x("i6","A",w) =L= x("i6","A",w+1) ;

definez(i,j).. z(i,j)*card(w) =G= sum(w,x(i,j,w));

atmostonej(i).. sum(j$ijok(i,j), z(i,j)) =L= 1;

MODEL priority /all/ ;

SOLVE priority using MIP maximizing obj;

parameter npvperscen(w);
npvperscen(w)= sum((i,j)$ijok(i,j), a(i,j)*x.l(i,j,w)) ;

display npvperscen;

display obj.l;
display s.l;
display x.l;
display z.l;

display drocon.m;
 
 
