#the following import is optional
#it only allows "intelligent" IDEs (like PyCharm) to support you in using it
from avnav_api import AVNApi
import math
import time
import os
from datetime import date
import xml.etree.ElementTree as ET
import urllib.request, urllib.parse, urllib.error
import json
import sys
from _ast import Try
import traceback
import time
try:
    from avnrouter import AVNRouter, WpData
    from avnav_worker import AVNWorker, WorkerParameter, WorkerStatus
except:
    pass
MIN_AVNAV_VERSION="20220426"

    #// https://www.rainerstumpe.de/HTML/wind02.html
    #// https://www.segeln-forum.de/board1-rund-ums-segeln/board4-seemannschaft/46849-frage-zu-windberechnung/#post1263721

    #//http://www.movable-type.co.uk/scripts/latlong.html
    #//The longitude can be normalised to −180…+180 using (lon+540)%360-180


class Plugin(object):
  PATHTSS="gps.TSS"   #    TrueWindAngle PT1 gefiltert
  PATHTLL_SB="gps.LLSB" #    Winkel Layline Steuerbord
  PATHTLL_BB="gps.LLBB" #    Winkel Layline Backbord
  PATHTLL_VPOL="gps.VPOL" #  Geschwindigkeit aus Polardiagramm basierend auf TWS und TWA 
  PATHTLL_OPTVMC="gps.OPTVMC" #  Geschwindigkeit aus Polardiagramm basierend auf TWS und TWA 
#  PATHTLL_speed="gps.speed" #  Geschwindigkeit aus Polardiagramm basierend auf TWS und TWA 


  CONFIG = [
      {
      'name':'TWD_filtFreq',
      'description':'Limit Frequency for PT1-Filter of TWD',
      'default':'0.2',
      'type': 'FLOAT'
      },
      ]

  
  @classmethod
  def pluginInfo(cls):
    """
    the description for the module
    @return: a dict with the content described below
            parts:
               * description (mandatory)
               * data: list of keys to be stored (optional)
                 * path - the key - see AVNApi.addData, all pathes starting with "gps." will be sent to the GUI
                 * description
    """
    return {
      'description': 'a test plugins',
      'version': '1.0',
      'config': cls.CONFIG,
      
      'data': [
        {
          'path': cls.PATHTSS,
          'description': 'TrueWindAngle PT1 filtered',
        },
        {
          'path': cls.PATHTLL_OPTVMC,
          'description': 'optimum vmc direction',
        },
        {
          'path': cls.PATHTLL_SB,
          'description': 'Layline Steuerbord',
        },
        {
          'path': cls.PATHTLL_BB,
          'description': 'Layline Backbord',
        },
        {
          'path': cls.PATHTLL_VPOL,
          'description': 'Speed aus Polare',
        },
      ]
    }




  def __init__(self,api):
    """
        initialize a plugins
        do any checks here and throw an exception on error
        do not yet start any threads!
        @param api: the api to communicate with avnav
        @type  api: AVNApi
    """
    
    self.api = api # type: AVNApi
    if(self.api.getAvNavVersion() < int(MIN_AVNAV_VERSION)):
        raise Exception("SegelDisplay-Plugin is not available for this AvNav-Version")
        return 

    self.api.registerEditableParameters(self.CONFIG, self.changeParam)
    self.api.registerRestart(self.stop)

    vers=self.api.getAvNavVersion()
    #we register an handler for API requests
    self.api.registerRequestHandler(self.handleApiRequest)
    self.count=0
    self.windAngleSailsteer={'x':0,'y':0, 'alpha':0}
    self.api.registerRestart(self.stop)
    self.oldtime=0
    self.polare={}
    if not self.Polare('polare.xml'):
       raise Exception("polare.xml not found Error")
       return
    self.saveAllConfig()
    self.startSequence = 0



  def getConfigValue(self, name):
    defaults = self.pluginInfo()['config']
    for cf in defaults:
      if cf['name'] == name:
        return self.api.getConfigValue(name, cf.get('default'))
    return self.api.getConfigValue(name)
  
  def saveAllConfig(self):
    d = {}
    defaults = self.pluginInfo()['config']
    for cf in defaults:
      v = self.getConfigValue(cf.get('name'))
      d.update({cf.get('name'):v})
    self.api.saveConfigValues(d)
    return 
  
  def changeConfig(self, newValues):
    self.api.saveConfigValues(newValues)
  
  def changeParam(self,param):
    self.api.saveConfigValues(param)
    self.startSequence+=1  
  
  def stop(self):
    pass

  def PT_1funk(self, f_grenz, t_abtast, oldvalue, newvalue):
    #const t_abtast= globalStore.getData(keys.properties.positionQueryTimeout)/1000 //[ms->s]
    T = 1 / (2*math.pi*f_grenz)
    tau = 1 / ((T / t_abtast) + 1)
    return(oldvalue + tau * (newvalue - oldvalue))


  def run(self):
    """
    the run method
    @return:
    """
    seq=0
    self.api.log("started")
    self.api.setStatus('STARTED', 'running')
    gpsdata={}
    while not self.api.shouldStopMainThread():
      time.sleep(0.5)  
      #gpsdata=self.api.getDataByPrefix('gps')
      gpsdata['track']=self.api.getSingleValue('gps.track')
      gpsdata['windAngle']=self.api.getSingleValue('gps.windAngle')
      gpsdata['windSpeed']=self.api.getSingleValue('gps.windSpeed')
      gpsdata['speed']=self.api.getSingleValue('gps.speed')

      calcTrueWind(self, gpsdata)          
      if 'AWS' in gpsdata and 'AWD' in gpsdata and 'TWA' in gpsdata and 'TWS' in gpsdata:
            best_vmc_angle(self,gpsdata)
            if(calcSailsteer(self, gpsdata)):
                self.api.addData(self.PATHTSS,gpsdata['TSS'])
                if calc_Laylines(self,gpsdata):  
                    self.api.setStatus('NMEA', 'computing Laylines/TSS/VPOL')
      else:
          self.api.setStatus('INACTIVE', 'Missing Input of windAngle and/or windSpeed, cannot compute Laylines')


  
  
 #https://stackoverflow.com/questions/4983258/python-how-to-check-list-monotonicity
  def strictly_increasing(self, L):
        return all(x<y for x, y in zip(L, L[1:]))
  

  def Polare(self, f_name):
    #polare_filename = os.path.join(os.path.dirname(__file__), f_name)
    polare_filename = os.path.join(self.api.getDataDir(),'user','viewer','polare.xml')
    try:
        tree = ET.parse(polare_filename)
    except:
            try:
                source=os.path.join(os.path.dirname(__file__), f_name)
                dest=os.path.join(self.api.getDataDir(),'user','viewer','polare.xml')
                with open(source, 'rb') as src, open(dest, 'wb') as dst: dst.write(src.read())
                tree = ET.parse(polare_filename)
            except:
                return False
    finally:
            if not 'tree' in locals():
                return False
            root = tree.getroot()
            x=ET.tostring(root, encoding='utf8').decode('utf8')
            e_str='windspeedvector'
            x=root.find('windspeedvector').text
        # whitespaces entfernen
            x="".join(x.split())
            self.polare['windspeedvector']=list(map(float,x.strip('][').split(',')))
            if not self.strictly_increasing(self.polare['windspeedvector']):
                raise Exception("windspeedvector in polare.xml IS NOT STRICTLY INCREASING!")
                return(False)

            e_str='windanglevector'
            x=root.find('windanglevector').text
        # whitespaces entfernen
            x="".join(x.split())
            self.polare['windanglevector']=list(map(float,x.strip('][').split(',')))
            if not self.strictly_increasing(self.polare['windanglevector']):
                raise Exception("windanglevector in polare.xml IS NOT STRICTLY INCREASING!")
                return(False)
            
            e_str='boatspeed'
            x=root.find('boatspeed').text
        # whitespaces entfernen
            z="".join(x.split())
        
            z=z.split('],[')
            boatspeed=[]
            for elem in z:
                zz=elem.strip('][').split(',')
                boatspeed.append(list(map(float,zz)))
            self.polare['boatspeed']=boatspeed
    
    
            e_str='wendewinkel'
            x=root.find('wendewinkel')
        
            e_str='upwind'
            y=x.find('upwind').text
        # whitespaces entfernen
            y="".join(y.split())
            self.polare['ww_upwind']=list(map(float,y.strip('][').split(',')))
    
            e_str='downwind'
            y=x.find('downwind').text
        # whitespaces entfernen
            y="".join(y.split())
            self.polare['ww_downwind']=list(map(float,y.strip('][').split(',')))
    return(True)

    
#https://appdividend.com/2019/11/12/how-to-convert-python-string-to-list-example/#:~:text=To%20convert%20string%20to%20list,delimiter%E2%80%9D%20as%20the%20delimiter%20string.        

  def handleApiRequest(self,url,handler,args):
    """
    handler for API requests send from the JS
    @param url: the url after the plugin base
    @param handler: the HTTP request handler
                    https://docs.python.org/2/library/basehttpserver.html#BaseHTTPServer.BaseHTTPRequestHandler
    @param args: dictionary of query arguments
    @return:
    """
    out=urllib.parse.parse_qs(url)
    out2=urllib.parse.urlparse(url)
    if url == 'test':
      return {'status':'OK'}
    if url == 'parameter':
      #self.count=0
      defaults = self.pluginInfo()['config']
      b={}
      for cf in defaults:
          v = self.getConfigValue(cf.get('name'))
          b.setdefault(cf.get('name'), v)
      b.setdefault('server_version', self.api.getAvNavVersion())
      return(b)
    return {'status','unknown request'}


def bilinear(self,xv, yv, zv, x, y) :
    #ws = xv
 try:
    angle =yv
    speed =zv
    #var x2i = ws.findIndex(this.checkfunc, x)
    x2i = list(filter(lambda lx: xv[lx] >= x, range(len(xv))))
    if(len(x2i) > 0):
        x2i = 1 if x2i[0] < 1 else x2i[0]
        x2 = xv[x2i]
        x1i = x2i - 1
        x1 = xv[x1i]
    else:
        x1=x2=xv[len(xv)-1]
        x1i=x2i=len(xv)-1

    #var y2i = angle.findIndex(this.checkfunc, y)
    y2i = list(filter(lambda lx: angle[lx] >= y, range(len(angle))))
    if(len(y2i) > 0):
        y2i = 1 if y2i[0] < 1 else y2i[0]
        #y2i = y2i < 1 ? 1 : y2i
        y2 = angle[y2i]
        y1i = y2i - 1
        y1 = angle[y2i - 1]
    else:
        y1=y2=angle[len(angle)-1]
        y1i=y2i=len(angle)-1

    ret =   \
             ((y2 - y) / (y2 - y1)) *   \
        (((x2 - x) / (x2 - x1)) * speed[y1i][x1i]  +    \
            ((x - x1) / (x2 - x1)) * speed[y1i][x2i])  +    \
        ((y - y1) / (y2 - y1)) *    \
        (((x2 - x) / (x2 - x1)) * speed[y2i][x1i]  +    \
            ((x - x1) / (x2 - x1)) * speed[y2i][x2i]) 
    return ret
 except:
        self.api.error(" error calculating bilinear interpolation for TWS with "+str(x)+"kn  at "+str(y)+"°\n")
        return(0)

  
def linear(x, x_vector, y_vector):

    #var x2i = x_vector.findIndex(this.checkfunc, x)
    #https://www.geeksforgeeks.org/python-ways-to-find-indices-of-value-in-list/
    # using filter()
    # to find indices for 3
    try:
        x2i = list(filter(lambda lx: x_vector[lx] >= x, range(len(x_vector))))
    # y_vector = BoatData.Polare.wendewinkel.upwind;
    #x2i = x2i < 1 ? 1 : x2i
        if(len(x2i) > 0):
           x2i = 1 if x2i[0] < 1 else x2i[0]
           x2 = x_vector[x2i]
           y2 = y_vector[x2i]
           x1i = x2i - 1
           x1 = x_vector[x1i]
           y1 = y_vector[x1i]
           y = ((x2 - x) / (x2 - x1)) * y1 + ((x - x1) / (x2 - x1)) * y2
        else:
            y=y_vector[len(y_vector)-1]
    except:
        self.api.error(" error calculating linear interpolation "+ "\n")
        return 0
    return y

def calc_Laylines(self,gpsdata):# // [grad]
    
    
    if (self.Polare and 'TWA' in gpsdata):
        # LAYLINES
        if (math.fabs(gpsdata['TWA']) > 120 and math.fabs(gpsdata['TSS']) < 240): 
            wendewinkel = linear((gpsdata['TWS'] / 0.514),self.polare['windspeedvector'],self.polare['ww_downwind']) * 2
        else:
            wendewinkel = linear((gpsdata['TWS'] / 0.514),self.polare['windspeedvector'],self.polare['ww_upwind']) * 2

        #LL_SB = (gpsdata['TWD'] + wendewinkel / 2) % 360
        #LL_BB = (gpsdata['TWD'] - wendewinkel / 2) % 360

        LL_SB = (gpsdata['TSS'] + wendewinkel / 2) % 360
        LL_BB = (gpsdata['TSS'] - wendewinkel / 2) % 360
        
        
        self.api.addData(self.PATHTLL_SB,LL_SB)
        self.api.addData(self.PATHTLL_BB,LL_BB)


        gpsdata['TWA']=gpsdata['TWA']%360
        anglew = 360 - gpsdata['TWA'] if gpsdata['TWA'] > 180 else gpsdata['TWA']
        #in kn
        if not self.polare['boatspeed']:
            return False
        SOGPOLvar = bilinear(self,  \
            self.polare['windspeedvector'],    \
            self.polare['windanglevector'],    \
            self.polare['boatspeed'],  \
            (gpsdata['TWS'] / 0.514), \
            anglew  \
        )
        self.api.addData(self.PATHTLL_VPOL,SOGPOLvar*0.514444)
        #self.api.ALLOW_KEY_OVERWRITE=True
        #allowKeyOverwrite=True
        #self.api.addData(self.PATHTLL_speed,SOGPOLvar*0.514444)
        return True
        
        # http://forums.sailinganarchy.com/index.php?/topic/132129-calculating-vmc-vs-vmg/
#VMG = BS * COS(RADIANS(TWA))
#VMC = BS * COS(RADIANS(BRG-HDG))
      
        #rueckgabewert = urllib.request.urlopen('http://localhost:8081/viewer/avnav_navi.php?request=route&command=getleg')
        #route=rueckgabewert.read()
        #inhalt_text = route.decode("UTF-8")
        #d = json.loads(inhalt_text)
        #VMCvar = ((gpsdata['speed'] * 1.94384) * math.cos((xx-gpsdata['track']) * math.pi) / 180)
    #print(d)

    
    
def calcSailsteer(self, gpsdata):
    rt=gpsdata
    if not 'track' in gpsdata or not 'AWD' in gpsdata:
        return False
    try:
        KaW=polar(gpsdata['AWS'], gpsdata['AWD']).toKartesisch()
        KaB = polar(gpsdata['speed'], gpsdata['track']).toKartesisch()


        t_abtast=(time.time()-self.oldtime)
        freq=1/t_abtast
        self.oldtime=time.time()
      
        fgrenz=float(self.getConfigValue('TWD_filtFreq'))
        self.windAngleSailsteer['x']=self.PT_1funk(fgrenz, t_abtast, self.windAngleSailsteer['x'], KaW['x'] - KaB['x'])
        self.windAngleSailsteer['y']=self.PT_1funk(fgrenz, t_abtast, self.windAngleSailsteer['y'], KaW['y'] - KaB['y'])
      # zurück in Polaren Winkel
        self.windAngleSailsteer['alpha']=kartesisch(self.windAngleSailsteer['x'],self.windAngleSailsteer['y']).toPolar()
        gpsdata['TSS']=self.windAngleSailsteer['alpha']
        
        return True
    except Exception:
        gpsdata['TSS']=0
        self.api.error(" error calculating TSS ")
        return False
    
def calcTrueWind(self, gpsdata):
    # https://www.rainerstumpe.de/HTML/wind02.html
    # https://www.segeln-forum.de/board1-rund-ums-segeln/board4-seemannschaft/46849-frage-zu-windberechnung/#post1263721      
        source='SegelDisplay'

        if not 'track' in gpsdata or not 'windAngle' or not 'speed' in gpsdata:
            return False
        gpsdata['AWA']=gpsdata['windAngle']
        gpsdata['AWS']=gpsdata['windSpeed']
        #self.api.addData(self.PATHAWA, gpsdata['AWA'],source=source)
        #self.api.addData(self.PATHAWS, gpsdata['AWS'],source=source)
        try:
            gpsdata['AWD'] = (gpsdata['AWA'] + gpsdata['track']) % 360
            #self.api.addData(self.PATHAWD, gpsdata['AWD'],source=source)
            KaW=polar(gpsdata['AWS'], gpsdata['AWD']).toKartesisch()
            KaB = polar(gpsdata['speed'], gpsdata['track']).toKartesisch()

            if(gpsdata['speed'] == 0 or gpsdata['AWS'] == 0):
                gpsdata['TWD'] = gpsdata['AWD'] 
                #self.api.addData(self.PATHTWD, gpsdata['TWD'],source=source)
            else:
                gpsdata['TWD'] = kartesisch(KaW['x'] - KaB['x'], KaW['y'] - KaB['y']).toPolar() % 360
            #self.api.addData(self.PATHTWD, gpsdata['TWD'],source=source)
            gpsdata['TWS'] = math.sqrt((KaW['x'] - KaB['x']) * (KaW['x'] - KaB['x']) + (KaW['y'] - KaB['y']) * (KaW['y'] - KaB['y']))
            #self.api.addData(self.PATHTWS, gpsdata['TWS'],source=source)

            gpsdata['TWA'] = LimitWinkel(self, gpsdata['TWD'] - gpsdata['track'])
            #self.api.addData(self.PATHTWA, gpsdata['TWA'],source=source)
            return True
        except Exception:
            self.api.error(" error calculating TrueWind-Data " + str(gpsdata) + "\n")
        return False
    
def LimitWinkel(self, alpha):  # [grad]   
    alpha %= 360
    if (alpha > 180): 
        alpha -= 360;
    return(alpha)  



class polar(object):
    def __init__(self,r, alpha):  # [alpha in deg] 
        self.r=r
        self.alpha=alpha
    def toKartesisch(self):
        K = {}
        K['x'] = self.r*math.cos((self.alpha * math.pi) / 180)
        K['y'] = self.r*math.sin((self.alpha * math.pi) / 180)
        return(K)    
        
class kartesisch(object):
    def __init__(self,x, y):  # [alpha in deg] 
        self.x=x
        self.y=y
    def toPolar(self):
        return(180 * math.atan2(self.y, self.x) / math.pi)
        K = {}
        K['x'] = self.r*math.cos((self.alpha * math.pi) / 180)
        K['y'] = self.r*math.sin((self.alpha * math.pi) / 180)
        return(K)    







try:
  import numpy as np
  from scipy.interpolate import InterpolatedUnivariateSpline

  def quadratic_spline_roots(self,spl):
    roots = []
    knots = spl.get_knots()
    for a, b in zip(knots[:-1], knots[1:]):
        u, v, w = spl(a), spl((a+b)/2), spl(b)
        t = np.roots([u+w-2*v, w-u, 2*v])
        t = t[np.isreal(t) & (np.abs(t) <= 1)]
        roots.extend(t*(b-a)/2 + (b+a)/2)
    return np.array(roots)

       

    
  def best_vmc_angle(self, gps):
    try:
      router=AVNWorker.findHandlerByName(AVNRouter.getConfigName())
      if router is None:
        return False
      wpData=router.getWpData()
      if wpData is None:
        return False
      if not wpData.validData and self.ownWpOffSent:
        return True
    except:
        return False

    try:
        self.cWendewinkel_upwind=[]
        self.cWendewinkel_downwind=[]
    
        lastindex=len(self.polare['windanglevector'])
    
        x = np.array(self.polare['boatspeed'])
        BRG=wpData.dstBearing
        windanglerad=np.deg2rad(BRG-gps['TWD']+np.array(self.polare['windanglevector']))
        coswindanglerad=np.cos(windanglerad)
    
        self.cWendewinkel_upwind=[]
        vmc=[]
        for i in range(len(self.polare['windspeedvector'])):
            updownindexvalue=next(z for z in self.polare['windanglevector'] if z >=90)
            updownindex=self.polare['windanglevector'].index(updownindexvalue, 0, lastindex)
            spalte=i
            # vmc=v*cos(BRG-HDG)
            # HDG = TWD +/- TWA
            # test: BRG = , TWD=0 --> HDG=-TWA --> vmc=v*cos(BRG+TWA)
            vmc.append(np.array(x[0:lastindex,spalte])*coswindanglerad[0:lastindex])
            f=InterpolatedUnivariateSpline(self.polare['windanglevector'], vmc[spalte][:], k=3)
            cr_pts = quadratic_spline_roots(self, f.derivative())
            cr_vals = f(cr_pts)
            min_index = np.argmin(cr_vals)
            max_index = np.argmax(cr_vals)
        #print("Maximum value {} at {}\nMinimum value {} at {}".format(cr_vals[max_index], cr_pts[max_index], cr_vals[min_index], cr_pts[min_index]))
            self.cWendewinkel_upwind.append(cr_pts[max_index])
        #Der TWA mit der höschsten VMC
        spl=InterpolatedUnivariateSpline(self.polare['windspeedvector'], self.cWendewinkel_upwind, k=3)
        wendewinkel = linear((gps['TWS'] / 0.514),self.polare['windspeedvector'],self.cWendewinkel_upwind)
        opttwa=spl(gps['TWS'] / 0.514)
        opthdg=(gps['TWD']-opttwa)%360
        diff1=abs((gps['TWD']-wendewinkel)%360-(gps['TWD']-opttwa)%360)
        # aus WA=WD-HDG folgt HDG = WD-WA
        #self.api.addData(self.PATHTLL_OPTVMC, (gps['TWD']-wendewinkel)%360,source='SegelDisplay')
        self.api.addData(self.PATHTLL_OPTVMC, (opthdg)%360,source='SegelDisplay')
    except:
        pass

    return(True)



except:
  def best_vmc_angle(self,gps):
      return False;
  pass    
    
