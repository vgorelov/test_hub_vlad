#
#                  === T-Server model for SIP ===
#
#
SESSION_PRIVATE_SERVICE_ID = 3004  #from 8.0.300.15 (was 4006)
SESSION_PRIVATE_INFO_ID = 4008  #from 8.0.300.15 (was 4006)

import model_tserver
import model_address
import model_address2

from  model_address import *
from  model_party_call import Party, Call
from  model_dn import DN, ServiceDN
from  model_queue_agent import Queue, Agent
from  model_routedn import RouteDN
from model_trunk import Trunk

if GetOption("MistPort") and GetOption("MistHost"):
    from  model_sipphone_mist import SipPhoneMist, GetMistConnection
from  model_sipphone_epi import SipPhoneEpi, SipPhoneEpiPipe, GetEpiConnection, epiConnections


SipTreatmentEvent = TreatmentEvent + ("CollectedDigits", )


class DummyPartyForLink():
    def __init__(self):
        PrintLog("Dummy party constructed")

    def get_link_by_property(self, propertyName):
        return [], []

    def get_link_by_property_reverse(self, propertyName):
        return None

    def clean_link_by_property(self, propertyName):
        pass

    def set_link_by_property(self, otherParty, propertyName):
        PrintLog("Dummy set_link_by_property() called")
        pass

class SIP_SupervisionSubscription(object):
    """Supervision subscription session class.
    Describes connection between SV and agent"""
    agentDN = None
    svDN = None
    svMode = "coach"
    svScope = "call"
    oneCall = True
    location = None

    def __init__(self, agentDN=None, svDN=None, svMode="coach", svScope="call", oneCall=True, location=None):
        self.agentDN = agentDN
        self.svDN = svDN
        self.svMode = svMode
        self.svScope = svScope
        self.oneCall = oneCall
        self.location = location


    def callSupervisor(self, party, addPrm = {}):
        pass


    def isSupervisorAbleToIntrude(self, call):
        if len(self.svDN.partyList):
            return False
        return True



class SIP_Party(Party):
    def __init__(self, call, dn, role, state):
        Party.__init__(self, call, dn, role, state)
        self.cdnPt = None  # Connects Queue party with Supplementary party
        self.queuePt = None
        self.chatSession = None
        self.muted_to_restore = 0


    # vgratsil, CPTT-119, 10-07-2014
    # added convenience methods
    # also introduced "party links by property"
    # "Party1 linked with Party2 by property N" means that Party1.N = Party2 for same site scenaios
    # for multisite scenarios it means that Party1 is linked with Trunk1 party, which points to Trunk2, and Party2
    # is linked with Trunk2 party by property N

    def get_other_end_trunk_party(self, otherDN):
        """
        Returns other end trunk party for case if self is participating in multisite call
        :param otherDN: DN object to which site find trunk
        :return: Party object, corresponding to other site trunk party. If not found - None
        """
        thisSiteTrunkParty = self.Call.findTrunkPartyToOtherSite(otherDN)
        return (thisSiteTrunkParty.DN.otherEndParty() if thisSiteTrunkParty and thisSiteTrunkParty.DN else None)

    def get_other_end_real_parties(self, otherDN):
        """
        Returns non-trunk parties from other-end call.
        :param otherDN: destination DN to find other-end connected call to
        :return: list of Party objects
        """
        otherEndTrunkPt = self.get_other_end_trunk_party(otherDN)
        if isinstance(self.DN, Trunk0):
            otherEndTrunkPt = self.DN.otherEndParty()
        if not otherEndTrunkPt:
            return []
        return otherEndTrunkPt.Call.findNonTrunkParties()


    def is_linked_by_property(self, otherPt, propertyName):
        """
        Tests if self has link with otherPt by property propertyName.
        If both self and otherPt of same site, then verifies if otherPt's property with name 'propertyName' set to self
        In case of multisite calls, verifies if otherPt's property with name 'propertyName' is set to corresponding trunk
        party
        :param otherPt: Party object
        :param propertyName: string. otherPt's property name to verify
        :return: True or False
        """
        if otherPt == None or propertyName == None:
            return False
        if self.tserver is getattr(otherPt, "tserver", None): #same site
            if getattr(otherPt, propertyName, None) is self:
                return True
            return False
        # else is multisite
        otherDN = otherPt.DN
        thisTrunkParty = self.Call.findTrunkPartyToOtherSite(otherDN)
        otherTrunkParty = self.get_other_end_trunk_party(otherDN)
        if (getattr(thisTrunkParty, propertyName, None) is self and
                    getattr(otherPt, propertyName, None) is otherTrunkParty):
            return True
        return False

    def set_link_by_property(self, otherPt, propertyName):
        """
        Sets self as link with otherPt by property propertyName.
        If both self and otherPt of same site, then sets otherPt's property with name 'propertyName' to self
        In case of multisite calls, sets otherPt's property with name 'propertyName' to corresponding trunk
        party
        :param otherPt: Party object or list of Parties objects
        :param propertyName: string, otherPt's property name to set
        :return: None
        """
        otherPt = otherPt if isinstance(otherPt, (tuple, list)) else (otherPt, )
        for oPt in otherPt:
            if self.tserver is getattr(oPt, "tserver", None): #same site
                setattr(oPt, propertyName, self)
                continue
            otherDN = getattr(oPt, "DN", None)
            thisTrunkParty = self.Call.findTrunkPartyToOtherSite(otherDN)
            otherTrunkParty = self.get_other_end_trunk_party(otherDN)
            setattr(thisTrunkParty, propertyName, self)
            setattr(oPt, propertyName, otherTrunkParty)

    def get_link_by_property(self, propertyName):
        """
        Gets linked parties by property 'propertyName'.
        If self has this property set to non-None value, then returns linked party, resolved in 'reverse' order
        Otherwise, returns all parties linked with self (same site or multi site)
        :param propertyName: name of the property to calculate link by
        :return: Two lists: first - same site linked parties, second - multisite linked parties. No Trunk parties
        included
        """
        thisProperty = getattr(self, propertyName, None)
        if thisProperty:
            # start reverse lookup
            if isinstance(thisProperty.DN, Trunk0): #multisite link
                return [], [getattr(thisProperty.DN.otherEndParty(), propertyName, None)]
            return [thisProperty], [] #same site link
        # first look for all the parties on this call with property set to self
        thisSiteParties = [party for party in self.Call.PartyList if getattr(party, propertyName, None) is self]
        thisSiteNonTrunkParties = [party for party in thisSiteParties if not isinstance(party.DN, Trunk0)]
        otherSiteTrunksToTest = [party.DN.otherEndParty() for party in thisSiteParties if isinstance(party, Trunk0)]
        otherSitesNonTrunkParties = []
        for otherSiteTrunkParty in otherSiteTrunksToTest:
            otherSitesNonTrunkParties += [party for party in otherSiteTrunkParty.Call.PartyList
                                          if getattr(party, propertyName, None) is otherSiteTrunkParty]
        return thisSiteNonTrunkParties, otherSitesNonTrunkParties

    def get_link_by_property_reverse(self, propertyName):
        """
        Gets reverse linked party by property 'propertyName'
        :param propertyName: name of the property to calculate link by
        :return: Party or None
        """
        if getattr(self, propertyName, None):
            tmpRes = self.get_link_by_property(propertyName)
            parties = tmpRes[0] if len(tmpRes[0]) else tmpRes[1]
            if len(parties) == 1:
                return parties[0]
        return DummyPartyForLink()

    def clean_link_by_property(self, propertyName, otherPt=None):
        """
        Resets link with otherPt by property propertyName to None.
        If both self and otherPt of same site, then sets otherPt's property with name 'propertyName' to None
        In case of multisite calls, resets all Trunk parties and otherPt's property with name 'propertyName' to None
        :param otherPt: Party object
        :param propertyName: string, otherPt's property name to set
        :return: None
        """
        if not otherPt:
            setattr(self, propertyName, None)
            return
        if self.tserver is getattr(otherPt, "tserver", None): #same site
            setattr(otherPt, propertyName, None)
            return
        otherDN = getattr(otherPt, "DN", None)
        thisTrunkParty = self.Call.findTrunkPartyToOtherSite(otherDN)
        setattr(thisTrunkParty, propertyName, None)
        setattr(otherPt, propertyName, None)

    def Close(self):
        if self.cdnPt:
            self.cdnPt.queuePt = None
        if self.queuePt:
            self.queuePt.cdnPt = None
        self.cdnPt = None
        self.queuePt = None
        self.chatSession = None
        Party.Close(self)

    def is_able_to_change_mute_state(self, mute_state):
        if mute_state != self.muted:
            if mute_state in (MuteState.Connect, MuteState.Muted):
                return True
            elif mute_state == MuteState.Coach and self.supervisorForPt:
                return True
            else:
                return False
        else:
            return False

    def change_mute_state(self, mute_state):
        self.muted = mute_state
        if self.DN.supervisorForLocation:
            trunkparty = self.Call.findTrunkParty()
            if trunkparty.supervisorForPt == self.supervisorForPt:
                trunkparty.muted = mute_state
                otherTrunkPt = self.Call.findTrunkPartyOnOtherSite()
                otherTrunkPt.muted = mute_state
        return True




class SIP_Call(Call):
    def __init__(self, tserver):

        self.CallSessionController = None
        self.toHg = False
        self.pendingQueue = None
        self.release_counter_on_hg = 0
        self.monitorMode = None
        Call.__init__(self, tserver)
        self.UserData = {}
        self.origUserData = {}
        self.pendingAttachDataChanged = False


    def findSupervisorParty(self):
        for pt in self.PartyList:
            if pt.supervisorForPt:
                return pt

    def findTrunkParty(self):
        for pt in self.PartyList:
            if isinstance(pt.DN, Trunk0):
                return pt

    def findTrunkPartyOnOtherSite(self):
        return self.findTrunkParty().DN.otherEndParty()

    def findTrunkPartyToOtherSite(self, dest):
        """
        finds trunk from call, that points to dest
        :param dest: device, to which we need to find trunk to
        :return: SIP_Party instance, that corresponds to the trunk, which connects multisite call. None if not found
        """
        if self.tserver is dest.tserver:
            return None
        PrintLog("1")
        def test_otherendparty_tserver(party):
            if isinstance(party.DN, Trunk0) and party.DN.otherEndParty():
                other_party = party.DN.otherEndParty()
                if other_party.DN.tserver:
                    return True
            return False

        found_trunk_parties = [party for party in self.PartyList if test_otherendparty_tserver(party) and
                                party.DN.otherEndParty().DN.tserver is dest.tserver]
        return found_trunk_parties[0] if len(found_trunk_parties) else None

    def findSupervisedParty(self, multisite=0):
        if not multisite:
            for pt in self.PartyList:
                if pt.DN.supervisedBy:
                    return pt
        else:
            trunkpt = self.findTrunkPartyOnOtherSite()
            return trunkpt.Call.findSupervisedParty()

    def findSIPSupplementaryParty(self):
        for pt in self.PartyList:
            if isinstance(pt.DN, SIP_Supplementary):
                return pt

    def findBridgerParty(self):
        for pt in self.PartyList:
            if pt.bridger:
                return pt

    def findDistributionDeviceParties(self):
        distr_parties_list = []
        for pt in self.PartyList:
            if isinstance(pt.DN, SIP_RouteDN) or isinstance(pt.DN, SIP_Queue):
                distr_parties_list.append(pt)
        return distr_parties_list

    def findDNandTrunkParties(self):
        dn_trunk_parties = []
        for pt in self.PartyList:
            if not(isinstance(pt.DN, SIP_RouteDN) or isinstance(pt.DN, SIP_Queue)):
                dn_trunk_parties.append(pt)
        return dn_trunk_parties

    def findNonTrunkParties(self):
        return [pt for pt in self.PartyList if not isinstance(pt.DN, Trunk)]


    def realPartiesList(self):
        """returns number of parties without Observer or supplementary cdn"""
        partyList = copy.copy(self.PartyList)

        for pt in self.PartyList:
            if pt.supervisorForPt or isinstance(pt.DN, SIP_Supplementary) or pt.bridger or isinstance(pt.DN,
                                                                                                      SIP_RouteDN):  #vlad gorelov fix 02/07/14
                del partyList[partyList.index(pt)]

        partyList1 = copy.copy(partyList)
        return partyList1

    def set_call_connection(self, otherCall):
        if not self.is_call_connected(otherCall):
            self.connectedCalls.append(otherCall)

    def is_call_connected(self, otherCall):
        if not otherCall in self.connectedCalls:
            return False
        return True

    def remove_call_connection(self, otherCall):
        if self.is_call_connected(otherCall):
            self.connectedCalls.remove(otherCall)

    def connect_calls(self, otherCall):
        self.set_call_connection(otherCall)
        otherCall.set_call_connection(self)

    def disconnect_calls(self, otherCall):
        self.remove_call_connection(otherCall)
        otherCall.remove_call_connection(self)

    def are_calls_connected(self, otherCall):
        if self.is_call_connected(otherCall) and otherCall.is_call_connected(self):
            return True
        return False

    def NotifyAboutPartyDeletion(self, addPrm=None):
        """
        Param:
          addPrm - parameter for event testing
        """

        if len(self.PartyList) == 1:
            if not addPrm or not addPrm.has_key("onlyInConference"):
                pt = self.PartyList[0]
                pt.DN.lastPartyInCall(pt, addPrm=addPrm)
                self.Close()

        elif len(self.PartyList) > 1:
            i = 0
            while i < len(self.PartyList):
                pt = self.PartyList[i]
                if addPrm and addPrm.has_key("supervisorPtLeft"):
                    supervisorPtLeft = addPrm["supervisorPtLeft"]
                    addPrm["ThirdPartyDN"] = supervisorPtLeft.DN.number
                    addPrm["ThirdPartyDNRole"] = PartyRole.Observer
                if addPrm and addPrm.has_key("supervisedAgentLeft"):
                    #supervisedAgentLeft = addPrm["supervisedAgentLeft"]
                    addPrm["ThirdPartyDNRole"] = PartyRole.Observer
                if pt.DN.otherPartyDeletedFromCall(pt, addPrm=addPrm):
                    i = i + 1  #pt doesn't leave call
            #if addPrm and addPrm.has_key("supervisorPtToLeave"):
            #  supervisorPtToLeave = addPrm["supervisorPtToLeave"]
            #  if supervisorPtToLeave:
            #    supervisorPtToLeave.DN.leaveCall(supervisorPtToLeave, notify = 1)

            if len(self.PartyList) == 2:  #only two are left, one is SUPERVISOR, call ends sometimes
                if (self.PartyList[1].supervisorForPt or self.PartyList[0].supervisorForPt):
                    agentAndSuperviserOnly = 0
                    if self.PartyList[0].supervisorForPt:
                        supervisorPt = self.PartyList[0]
                        otherPt = self.PartyList[1]
                    else:
                        supervisorPt = self.PartyList[1]
                        otherPt = self.PartyList[0]

                    if (self.PartyList[0].DN.supervisedBy and self.PartyList[1].supervisorForPt) or \
                            (self.PartyList[1].DN.supervisedBy and self.PartyList[0].supervisorForPt):
                        agentAndSuperviserOnly = 1

                    addPrm["doNotTestOtherDN"] = 1
                    addPrm["OtherDN"] = "unknown"
                    addPrm["OtherDNRole"] = "unknown"

                    #print "supervisorPt.muted %d" %supervisorPt.muted
                    if agentAndSuperviserOnly:  # customer left
                        if supervisorPt.muted == 1 or supervisorPt.State == PartyState.Ringing:
                            # cannot hear or ringing, both leave
                            supervisorPt.DN.printLog(
                                "1 Suprevisor %s (mode = %s, muted = %s, state = %s) is leaving call because he is muted or ringing" % (
                                    supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted,
                                    supervisorPt.State))
                            supervisorPt.DN.leaveCall(supervisorPt, notify=1, addPrm=addPrm)  #supervisor leaves first
                        else:
                            supervisorPt.DN.printLog(
                                "1a Suprevisor %s (mode = %s, muted = %s, state = %s) is NOT leaving call because he is not muted or ringing" % (
                                    supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted,
                                    supervisorPt.State))
                    else:  ##superviser and somebody else (customer or not supervised agent)
                        if supervisorPt.muted in (1,
                                                  2) or supervisorPt.State == PartyState.Ringing:  #supervisor does not hear or it did not answer
                            supervisorPt.DN.printLog(
                                "2 Suprevisor %s (mode = %s, muted = %s, state = %s) is leaving call because he is muted/half muted or ringing" % (
                                    supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted,
                                    supervisorPt.State))
                            supervisorPt.DN.leaveCall(supervisorPt, notify=1, addPrm=addPrm)  #supervisor leaves first
                        else:  #mode connect - supervisor hear
                            pass  #stays
                            if supervisorPt.DN.monitorScope == "agent" and supervisorPt.DN.tserver.monitor_consult_calls != "none":
                                supervisorPt.DN.printLog("2a Suprev")
                                supervisorPt.DN.leaveCall(supervisorPt, notify=1, addPrm=addPrm)
                            else:
                                supervisorPt.DN.printLog(
                                    "2a Suprevisor %s (mode = %s, muted = %s, state = %s) is NOT leaving call because he is in mode connect" % (
                                        supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted,
                                        supervisorPt.State))
                                #supervisorPt.DN.printLog("2a Suprevisor %s (mode = %s, muted = %s, state = %s) is NOT leaving call because he is in mode connect" %(supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted, supervisorPt.State))
                                #if addPrm["customerLeft"]: #if customer left superviser leaves
                                #  supervisorPt.DN.printLog("3 Suprevisor %s (mode = %s, muted = %s, state = %s) is leaving call because customer left" %(supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted, supervisorPt.State))
                                #  supervisorPt.DN.leaveCall(supervisorPt, notify = 1, addPrm = addPrm) #supervisor leaves first
                                #elif supervisorPt.DN.monitorRequestedBy == "agent":
                                #  supervisorPt.DN.printLog("4 Suprevisor %s (mode = %s, muted = %s, state = %s) is leaving call because it is agent assist" %(supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted, supervisorPt.State))
                                #  supervisorPt.DN.leaveCall(supervisorPt, notify = 1, addPrm = addPrm) #supervisor leaves first
                                #elif supervisorPt.DN.tserver.appVersion < "7.6":
                                #  if addPrm["supervisedAgentLeft"]:
                                #    supervisorPt.DN.printLog("5 Suprevisor %s (mode = %s, muted = %s, state = %s) is leaving call because it is 7.5 and supervisor does not talk to nonsupervised person" %(supervisorPt.DN.number, supervisorPt.DN.monitorMode, supervisorPt.muted, supervisorPt.State))
                                #    supervisorPt.DN.leaveCall(supervisorPt, notify = 1, addPrm = addPrm) #supervisor leaves first


                                #else: stays
                #two are left, one is bridger, call ends
                if addPrm["customerLeft"] and len(self.PartyList) == 2:
                    if self.PartyList[0].bridger:
                        self.PartyList[0].DN.leaveCall(self.PartyList[0], notify=1, addPrm=addPrm)
                    elif self.PartyList[1].bridger:
                        self.PartyList[1].DN.leaveCall(self.PartyList[1], notify=1, addPrm=addPrm)
                #two are left, one is gcti::record, call ends
                if len(self.PartyList) == 2 and (
                                self.PartyList[0].DN.number == "gcti::record" or self.PartyList[1].DN.number == "gcti::record"):
                    if self.PartyList[0].DN.number == "gcti::record":
                        self.PartyList[0].DN.leaveCall(self.PartyList[0], notify=1, addPrm=addPrm)
                    elif self.PartyList[1].DN.number == "gcti::record":
                        self.PartyList[1].DN.leaveCall(self.PartyList[1], notify=1, addPrm=addPrm)

    def Close(self, cause = None, mainCall = None):
        """ Erase self.
        Remove all reference from self and to self.
        """
        #if self.PartyList:
        #  print self
        ptList = copy.copy(self.PartyList); self.PartyList = []
        for pt in ptList:
            pt.Close()
        if self.created:
            addPrm = {}
            if cause:
                if not mainCall:
                    mainCall = self.MainCall
                    if mainCall:
                        addPrm["RefCallUUID"] = mainCall.CallUUID
                        mainCall.ownISLinkList = All([mainCall.ownISLinkList, self.ownISLinkList])
            #print self.ownISLinkListToString()
            for isLink in self.ownISLinkList.keys():
                #print isLink
                cl = self.ownISLinkList[isLink]
                #print cl.ownISLinkListToString()

                if cause and self.MainCall:
                    cl.ownISLinkList[isLink] = self.MainCall
                else:
                    if cl.ownISLinkList.has_key(isLink): #it does not if key == NewISLinkID
                        del cl.ownISLinkList[isLink]

            if not self.ghost:
                if self.tserver.CallReleaseTrackingFeature():
                    if self.ControlParty: addPrm["CtrlParty"] = self.ControlParty
                self.tserver.CallMonitoring.eventCallDeleted(self, addPrm = addPrm)

        #self.ConnID             = None
        self.PreviousConnID     = None
        self.Scope              = None
        self.Conference         = None
        self.ConsultType        = None
        self.PropagatedType     = None
        self.LocalType          = None
        self.ForwardingDN       = None
        self.LastForwardingDN   = None
        self.CalledPartyAddress = None
        self.MediaType          = None
        self.SessionID          = None
        self.GlobalCallID       = None
        self.ControlParty       = None

        # Remove consult call connection
        if self.MainCall:
            self.MainCall.ConsultList.remove(self)
        self.MainCall = None

        for cl in self.ConsultList:
            cl.MainCall = None
        self.ConsultList = []

        for cl in self.connectedCalls:
            if self in cl.connectedCalls:
                cl.connectedCalls.remove(self)
        self.connectedCalls = []

        self.ownISLinkList = {}

        self.tserver.remove_call(self)
        if sys.copyright.lower().find("python") > -1:
            gc.collect()

class SIP_Address:
    def __init__(self, number, tserver, sipNode, controllerType):
        if tserver:
            tserver.tableOfAddresses[number] = (sipNode, controllerType)

    def completeMakeCallDial(self, party, dest, addPrm = None):
        PrintLog("DEBUG: completeMakeCallDial: self {0}".format(self))
        if party.Call.ConsultType:
            mainCall = party.Call.MainCall
            mainParty = GetPartyByDN(self, mainCall)

            method = party.Call.userDataMethodForConsultCall
            PrintLog("party.Call.userDataMethodForConsultCall: {0}".format(method))
            if mainCall.pendingAttachDataChanged:
                self.doJointOnMainCall(party.Call)
                mainCall.pendingAttachDataChanged = False
            mainParty.stateChanged(held = 1)
            self.eventHeld(mainParty)
            for brPt in mainParty.bridgedPts:
                brPt.stateChanged(held = 1) # to receive Held state
                brPt.DN.eventHeld(brPt)

        party.addToCall(party.Call)
        if not addPrm:
            addPrm = {}
        if party.Call.ViaExtRouter:
            addPrm["OtherDN"] = "Trunk"
            party.Call.updateCallData()
            #if self.tserver.TransactionMonitoringFeature():
            #  transaction = self.tserver.TransactionMonitor.CreateTransaction(party.Call, "source")

        else:
            if addPrm.has_key("forwardedFrom"):
                addPrm["OtherDN"]  = addPrm["forwardedFrom"] # DN from which this call was forwarded
            else:
                addPrm["OtherDN"] = dest
        addPrm["OtherDNRole"] = PartyRole.Destination
        addPrm["ReferenceID"] = party.makeCallReferenceID
        if not self.offHook:
            self.waitEventSet(party, [EventName.OffHook, EventName.Dialing], addPrm = addPrm)
            self.offHook = 1

        else:
            self.eventDialing(party, addPrm = addPrm)

        return copy.copy(addPrm)


    def otherRealPartyFromCall(self, party, cl=None):
        #used in otherDN testing
        # oh. won't work with supplementary AND supervision
        if not cl:
            cl = party.Call

        if len(cl.PartyList) > 2:
            supplPt = cl.findSIPSupplementaryParty()
            if supplPt:
                if supplPt == party:
                    queuePt = party.queuePt
                    return queuePt.DN.otherRealPartyFromCall(queuePt)
                else:
                    partyList = cl.realPartiesList()
                    return self.otherPartyFromList(party, partyList)
            else:
                #no supplPt
                supervisorPt = cl.findSupervisorParty()
                if supervisorPt:
                    if party == supervisorPt:  # self is superviser
                        supervisedPt = party.supervisorForPt
                        if supervisedPt and supervisedPt.Call:  # it might be already gone
                            return supervisedPt.DN.otherRealPartyFromCall(supervisedPt)
                        else:
                            return Any()

                    else:
                        partyList = cl.realPartiesList()
                        return self.otherPartyFromList(party, partyList)
                else:
                    bridgerPt = cl.findBridgerParty()
                    if bridgerPt:
                        if party == bridgerPt:  # self is bridger
                            bridgedPt = cl.PartyList[-1]  #most likely
                            if bridgedPt <> party:
                                return bridgedPt.DN.otherRealPartyFromCall(bridgedPt)
                            else:
                                return Any()

                        else:
                            partyList = cl.realPartiesList()
                            return self.otherPartyFromList(party, partyList)
                    else:
                        partyList = cl.realPartiesList()
                        return self.otherPartyFromList(party, partyList)

        elif len(cl.PartyList) == 2:
            return self.otherPartyFromList(party, cl.PartyList)


class SIP_Trunk(Trunk):

    def __init__(self, orig, dest, owner = 1):
        PrintLog(orig)
        PrintLog(orig.tserver)
        PrintLog(dest)
        PrintLog(dest.tserver)
        Trunk.__init__(self, orig, dest, owner)
        other_tserver = orig.tserver if not (orig.tserver is self.tserver) else dest.tserver
        for ts in (self.tserver, other_tserver):
            PrintLog(str(ts))
            PrintLog(str(getattr(ts, "trunk_optimization", None)))
        self.optimized = False
        if self.tserver.is_optimized(other_tserver):
            self.optimized = True

    def cofEnabledOnBothSides(self):
        if InTrue(GetOption("CofFeature")):
            if (InTrue(self.orig.tserver.cof_feature) and InTrue(self.dest.tserver.cof_feature)) and \
                    (
                                    self.orig.tserver.default_network_call_id_matching == 'sip' and self.dest.tserver.default_network_call_id_matching == 'sip'):
                return 1
        return 0

    def establish(self, party, addPrm = None):
        if party.State!=PartyState.Dialing:
            cl = party.Call
            if cl and cl.pendingQueue:
                #and isinstance(cl.pendingQueue, SIP_RouteDN):
                cl.pendingQueue.distrCallToDestination(cl, self)
        DN.establish(self, party)
        self.establishTrunkSpecific(party, addPrm)

    def ring(self, call, callState = CallState.Ok, thisQueue = None, role = None, addPrm = None):
        if not call.ViaExtRouter and (self.orig.QueueToPass or self.cofEnabledOnBothSides()):
            call.ViaExtRouter = 1

        uui_key = self.tserver.UUIKey

        if not role:
            if len(call.PartyList) > 1 and call.Conference:
                role = PartyRole.ConferenceMember
            else:
                role = PartyRole.Destination
        newUserData = {}
        ringPt = self.createRingingParty(call, role)
        if call.ViaExtRouter:

            cast_type = self.tserver.cast_type
            if cast_type:
                cast_type = cast_type.split()[0]
            else:
                cast_type = "route"

            if self.orig.postponedEventRemoteConnectionSuccess:
                ev = self.orig.mayBeEvent(EventName.RemoteConnectionSuccess, None, timeout = 3)
                if ev: self.orig.postponedEventRemoteConnectionSuccess = 0
            origSwitch = self.orig.tserver.cfgSwitch
            destSwitch = self.dest.tserver.cfgSwitch
            viaExtRoutePoint = 0
            if origSwitch and not ((hasattr(self.dest, "QueueToPass") and self.dest.QueueToPass) or self.cofEnabledOnBothSides()):
                if origSwitch.switchAccessCodes:
                    for ac in origSwitch.switchAccessCodes:
                        if ac.switchDBID == destSwitch.DBID:
                            routeType = ac.routeType
                            if (routeType == CfgRouteType.CFGXRouteTypeDefault and cast_type == "route") or\
                              routeType == CfgRouteType.CFGXRouteTypeRoute:
                                viaExtRoutePoint = 1  # else consider it to be DIRECT, no ext RP involved
                            break

            if viaExtRoutePoint:
                if self.orig.tserver.CallMonitoringFeature() and self.dest.tserver.CallMonitoringFeature():

                    erp = self.dest.tserver.FindExtRoutePointOnCall()
                    erp.realDest = self.dest
                    self.dest = erp

            if call.MainCall:
                # ringing on consult call
                # determine UserData
                udataUpdate = call.UserData
                if call.userDataMethodForConsultCall == "joint":
                    if udataUpdate:
                        call.MainCall.pendingAttachDataChanged = True
                    call.UserData = call.MainCall.UserData
                elif call.userDataMethodForConsultCall == "inherited":
                    call.UserData = copy.deepcopy(call.MainCall.UserData)

                # local consult userdata
                call.UserData.update(udataUpdate)
                # determine ConnID
                if self.orig.tserver.use_data_from in ("original", "active-data-original-call"):
                    # userdata to be taken from main call
                    connID = call.MainCall.ConnID
                    lastTransferConnID = call.MainCall.ConnID
                else:#user data active
                    connID = call.ConnID
                    lastTransferConnID = call.ConnID

                if self.orig.tserver.use_data_from == "original":
                    newUserData = copy.deepcopy(call.MainCall.UserData)
                else:
                    newUserData = copy.deepcopy(call.UserData)
                    #userdata on main call is not updated yet
            else: #it is not consult call
                newUserData =  copy.deepcopy(call.UserData) #userdata on consul call is already updated
                connID = call.ConnID
                if cast_type == "reroute":
                    lastTransferConnID = NoConnID
                else:
                    lastTransferConnID = call.ConnID

            if connID <> NoConnID and (self.dest.tserver.hasCallsWithConnID(connID) or self.tserver.callFromOtherTserver(connID, self.dest.tserver)):
                connID = NoConnID

        else: # not an ISCC call
            if call.UserData and uui_key in call.UserData:
                newUserData = {self.tserver.UUIKey: call.UserData[self.tserver.UUIKey]}
            else:
                newUserData = {}
            connID = NoConnID
            lastTransferConnID = NoConnID

        otherTrunk = self.dest.tserver.Trunk(self.orig, self.dest, 2)
        self.otherTrunk = otherTrunk
        otherTrunk.otherTrunk = self

        if call.ViaExtRouter:

            #if (self.tserver.use_data_from  in ("consult-user-data", "current") and otherTrunk.tserver.use_data_from  in ("consult-user-data", "current")):
            self.viaISCC = 1
            otherTrunk.viaISCC = 1
            #else:
            #  if (self.tserver.use_data_from  in ("consult-user-data", "current")  or otherTrunk.tserver.use_data_from  in ("consult-user-data", "current")):
            #    FatalError("Misconfiguration, combination of options %s, %s is not supported" %(self.tserver.use_data_from, otherTrunk.tserver.use_data_from))

            if self.tserver.TransactionMonitoringFeature():
                transaction = self.tserver.TransactionMonitor.CreateTransaction(ringPt.Call, "source")

        newCall = otherTrunk.commonMakeCall(self.dest, inbound = 1, userData = newUserData, connID = connID, lastTransferConnID = lastTransferConnID)
        if call.transaction:
            if newCall:
                self.tserver.TransactionMonitor.UpdateTransitions(call.transaction, {'iscc.transaction': "completed", \
                                                                  'iscc.is-link-creation': "completed", \
                                                                  'iscc.call-operation': "completed", \
                                                                  'iscc.resource': "completed"})
            else:
                self.tserver.TransactionMonitor.UpdateTransitions(call.transaction, {'iscc.transaction': "terminated", \
                                                                  'iscc.is-link-creation': "terminated", \
                                                                  'iscc.call-operation': "terminated", \
                                                                  'iscc.resource': "terminated"})


        #if newCall:
        #  if call.MainCall and self.orig.tserver.use_data_from == "original":
        #    newCall.connectedCalls.append(call.MainCall)
        #    call.MainCall.connectedCalls.append(newCall)
        #  else:
        #    newCall.connectedCalls.append(call)
        #    call.connectedCalls.append(newCall)



        return ringPt


    def eventPartyChanged(self, party, printEventHdr=1, addPrm={}):
        if not (self.optimized or self.viaISCC):
            return
        equalize_connid_iscc = False
        if "equalize_connid_iscc" in addPrm:
            equalize_connid_iscc = addPrm["equalize_connid_iscc"]
        #time to update call with new connID/UD, because transfer or conference has been completed
        newConnID = party.Call.ConnID
        newUserData = party.Call.UserData
        newLocalType = party.Call.LocalType
        newPropagatedType = party.Call.PropagatedType
        otherEndPt = self.otherEndParty()
        otherEndPtCall = otherEndPt.Call

        party.Call.connect_calls(otherEndPtCall)
        if addPrm and addPrm.has_key("ThirdPartyDN"):
            oldThirdPartyDN = addPrm["ThirdPartyDN"]
            cleanThirdPartyDN = string.split(addPrm["ThirdPartyDN"], "::")[-1]
            if addPrm.has_key("ThirdPartyDNLocation"):
                addPrm["ThirdPartyDN"] = addPrm["ThirdPartyDNLocation"] + "::" + cleanThirdPartyDN
            else: addPrm["ThirdPartyDN"] = cleanThirdPartyDN
        if GetOption("Partitioning"):
            otherEndPtCall.PropagatedType = newPropagatedType

        if equalize_connid_iscc:
            PrintLog("DEBUG: equalization")
            addPrm["PreviousConnID"] = otherEndPtCall.ConnID
            otherEndPtCall.ConnID = party.Call.ConnID
            otherEndPtCall.newData = NewCallData(copy.deepcopy(newUserData))  # but still updates UserData
            otherEndPtCall.mayBeUpdateCallData()
        for pt in otherEndPtCall.PartyList:
            #TODO: fix LastTransferConnID handling
            otherEndPtCall.LastTransferConnID = NoConnID
            otherEndPtCall.PreviousConnID = NoConnID
            if party.Role == PartyRole.ConferenceMember:
                pt.Role = party.Role
            if pt.DN == self.otherTrunk:
                continue
            PrintLog("addPrm: {0}".format(addPrm))
            PrintLog(str(otherEndPtCall))
            PrintLog(str(otherEndPtCall.LastTransferConnID))
            # dirty hack by Vlad G
            """ev = None
            ev_to_putBack = pt.DN.tserver.WaitEvent(pt.DN.number, "PartyChanged", 2)
            #pt.DN.tserver.PutBackEvent(ev_to_putBack)
            if pt.DN.tserver.use_data_from == "current":
                ev = pt.DN.tserver.WaitEvent(pt.DN.number, "PartyChanged", 0.2)
            pt.DN.tserver.PutBackEvent(ev_to_putBack)
            if ev:
                pt.DN.tserver.PutBackEvent(ev)
            """
            # the end of dirty hack
            pt.DN.eventPartyChanged(pt, printEventHdr, addPrm=addPrm)
        if addPrm and addPrm.has_key("ThirdPartyDN"):
            addPrm["ThirdPartyDN"] = cleanThirdPartyDN

""" def leaveCall(self, party, notify = 1, abandPermited = 1, addPrm = None, cause = Cause.CauseEMPTY):
        print "debug22"
        if
        if party.Call.MainCall or len(party.Call.ConsultList) and self.tserver.monitor_consult_calls != "none":
        DN.leaveCall(self, party, notify, abandPermited, addPrm)
    #to avoid recursive call
        if notify:
            if self.otherTrunk.onlyParty():
                if addPrm:
                    if addPrm.has_key("OtherDN"): del addPrm["OtherDN"]
                    if addPrm.has_key("OtherDNRole"): del addPrm["OtherDNRole"]
                self.otherTrunk.onlyParty().DN.leaveCall(self.otherTrunk.onlyParty(), notify, abandPermited, addPrm) """


class SIP_DN(SIP_Address, DN):
    #def __init__(self, number, register = 1, numberForCall = None, numberForExtCall = None,
    #            gSipEndPointHost = "", gSipEndPointPort = 0, scsEndPoint = None, endpointType = None, tserver = None):


    def __init__(self, number, register=1, numberForCall=None, numberForExtCall=None,
                 scsEndPoint=None, endpointType="", endpointParams=None, tserver=None,
                 sipNode=None, controllerType="TController"):
        """number - string
          register - boolean
          numberForCall - string
          numberForExtCall - string
          scsEndPoint - reference to ScsObject, e.g application for EpiPhone.
          endpointType - None - hard phone, "epi" - epiphone, "gsip" - gsip endpoint
          endpointParams - dictionary in format {"EpiHost": host, "EpiPort": port} for "epi" endpointType
                                                {"gSipEndPointHost": host, "gSipEndPointPort": port} for "gsip" endpointType

          sipNode - reference to SipNode object, if not specified first Node in cluster is choosen
          controllerType - controller type for registering DN, default "TController"

        """
        SIP_Address.__init__(self, number, tserver, sipNode, controllerType)
        DN.__init__(self, number, register, numberForCall=numberForCall, numberForExtCall=numberForExtCall,
                    tserver=tserver)
        self.abandPermitedOnReleasingInRinging = 0
        self.URI = "sip:%s@%s" % (self.number, self.tserver.sipAddr)
        self.ipURI = "sip:%s@%s" % (self.number, self.tserver.sipIPAddr)
        self.contact = None
        self.scsEndPoint = scsEndPoint
        # fix by Vlad Gorelov 08/05/13. self.nailedup attribute is removed. Added attributes self.line_type and self.one_pcc_request
        # self.one_pcc_request attribute is needed to handle processing of AnswerCall and ReleaseCall in case that DN is nailed-up
        self.parked = 0
        self.one_pcc_request = 0
        self.line_type = "0"
        self.release_on_hg_call = 0
        self.monitorMode_to_restore = None
        self.monitorMode_in_request = None
        # end of fix
        if scsEndPoint:
            if hasattr(scsEndPoint, "DNs"):
                scsEndPoint.DNs.append(self)
            else:
                scsEndPoint.DNs = [self]
        self.endpointType = endpointType
        if self.cfgDN:
            self.contact = self.FindOption("TServer", "contact")
            # fix by Vlad Gorelov 08/05/13 line_type property setting
            self.line_type = self.FindOption("TServer", "line-type")
            # end of fix
        self.SipPhone = Any()
        if not endpointType:
            #This is hard phone !! always
            pass

        if endpointType.lower() in ("epi", "epipipe"):
            if not endpointParams:
                self.epiPort = GetOptionMandatory("EpiPort")
                self.epiHost = GetOptionMandatory("EpiHost")
            else:
                self.epiPort = endpointParams["EpiPort"]
                self.epiHost = endpointParams["EpiHost"]
            if endpointType.lower() == ("epi"):
                try:
                    self.SipPhone = SipPhoneEpi(self, self.epiHost,
                                                self.epiPort)  # which is not true in case of mix environment of hard phones and epiphones
                except ResetExcept, mess:
                    FatalError(mess)
            elif endpointType.lower() == ("epipipe"):
                try:
                    self.SipPhone = SipPhoneEpiPipe(self, self.epiHost,
                                                    self.epiPort)  # which is not true in case of mix environment of hard phones and epiphones
                except ResetExcept, mess:
                    FatalError(mess)
            if GetOption("EpiRequestTimout") != None:
                self.SipPhone.requestTimout = float(GetOption("EpiRequestTimout"))
            else:
                self.SipPhone.requestTimout = 0.5
            self.SipPhone.SipRegisterAll()

        elif endpointType.lower() == "gsip":
            from  model_gsipendpoint import GSipEndPoint

            self.SipPhone = GSipEndPoint(endpointParams["gSipEndPointHost"], endpointParams["gSipEndPointPort"], self)
            self.specialObject = self.SipPhone
        elif endpointType.lower() == "mist":
            if not endpointParams:
                if GetOption("MistPort") and GetOption("MistHost"):
                    if GetMistConnection():
                        self.SipPhone = SipPhoneMist(self)
            else:
                endpointParams["MistPort"]
                endpointParams["MistHost"]
                #todo - custom mist connection - vdrug ponadobitsya

        if GetOption("MistPort") and GetOption("MistHost"):
            if GetMistConnection():
                self.SipPhone = SipPhoneMist(self)

        self.autoAnswerOnSSC = False
        if number == "gcti::record" or number == "gcti::video":
            self.autoAnswerOnSSC = True
            #trick - do not create a separate class gcti:record but "mask" offhook/onhook
            def eventOffHook(self, party, printEventHdr=1, addPrm=None): """!pass"""  #***240

            def eventOnHook(self, party, printEventHdr=1, addPrm=None): """!pass"""  #***240

            self.eventOffHook = eventOffHook
            self.eventOnHook = eventOnHook

        self.lastSuperviserFor = None
        self.monitorScope = None
        self.supervisor_action_hold = None  #i.e. if the supervisor explicitly places his phone set on hold, then when the agent conference/alternates back/reconnects back the supervisor will continue to be on hold
        self.Mailbox = None
        if self.toRegister <> -1 and GetOption("PJSip"):
            from pjsipsim_model import PJSipSimulator

            self.SipPhone = PJSipSimulator(self)

    #fix by Vlad Gorelov 10/31/13. added method for setting default value of line-type property
    def setDefaultOptionValues(self):
        model_address.Address.setDefaultOptionValues(self)
        self.line_type = "0"

    # end of fix.


    def otherRealPartyFromCall(self, party, cl=None):
        if not cl:
            cl = party.Call
        if not cl.pendingQueue:
            return DN.otherRealPartyFromCall(self, party, cl)
        parties = [prt for prt in cl.PartyList if not (prt.DN in cl.pendingQueue.getHgMembers() + [cl.pendingQueue])]
        if len(parties): return parties[0]
        return None

    def isInCall(self):
        """
        returns tuple of current device states
        :return: tuple of boolean values: (does device have a parties, are there Established party)
        """
        if len(self.partyList) == 0:
            return False, False
        established_parties = [party for party in self.partyList if party.State == PartyState.Established]
        return True, len(established_parties) > 0


    def predictAgentEvent(self, cause, maxEvents=4):
        return None

    def eventRinging(self, party, printEventHdr=1, addPrm=None):
        if self.supervisionStarted == 1:
            addPrm["ThisDNRole"] = PartyRole.Observer
            party.Role = PartyRole.Observer
        PrintLog("SIP_DN - {0}".format(addPrm))
        return DN.eventRinging(self, party, printEventHdr, addPrm)

    def UsualCleanUp(self):
        DN.UsualCleanUp(self)
        if not isinstance(self.SipPhone, Any):
            self.SipPhone.UsualCleanUp()


    def Clear(self):
        DN.Clear(self)
        if not isinstance(self.SipPhone, Any):
            if not self.tserver.sipPhoneCleared:
                self.SipPhone.Clear()
        if self.lastSuperviserFor:
            self.tserver.CancelMonitoring(self.number, self.lastSuperviserFor.number)
            self.tserver.WaitEvent(timeout=1)
        if self.supervisorFor:
            self.supervisorFor.supervisedBy = None
            self.supervisorFor = None
        if self.supervisor_action_hold:
            self.supervisor_action_hold = None

    def Close(self):
        DN.Close(self)
        if not isinstance(self.SipPhone, Any):
            self.SipPhone.Close()

    def canAcceptCall(self, call=None):
        if self.busy:
            return 0
        elif len(self.partyList) < self.numberOfLines:
            return 1
        else:
            return 0

    def getAutoAnswer(self, call=None):
        pt, call = self.getPartyFromCall((), call)
        if call.MediaType == MediaType.Chat:
            return 1
            #fix by Vlad Gorelov 10/31/13. Handling of autoanswer if DN is nailed-up and parked
        if self.parked:
            return 1
        else:
            return self.autoAnswer
            #end of fix


    def canMakeCall(self):
        return 1


    def MakeCall(self, dest, location=None, userData=None, makeCallType=MakeCallType.Regular, extensions=None,
                 byDefault=0):
        userData = self.getUserData(1, userData)
        if location:
            loc = ", location %s" % location
        else:
            loc = ""
        if not byDefault:
            time.sleep(self.tserver.requestTimout)
            if self.tserver.SwitchPolicyFeature():
                request = self.tserver.MakeCall(self.number, self.destFullNum(dest, location), location, makeCallType,
                                                userData, extensions=extensions, send=0)
                self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, parties=self.partyList,
                                                                   targetDN=dest, expectedAvailability=True)

            refID = self.tserver.MakeCall(self.number, self.destFullNum(dest, location), location, makeCallType,
                                          userData, extensions=extensions)
            if not (byDefault == 2):  #2 means chat
                self.printLog("\nMakeCall(%s, dest = %s, refID = %s%s)" % (
                    self.number, self.destFullNum(dest, location), refID, loc))
            if location:
                self.remoteConnectionSuccessRefID = refID


        else:
            self.tserver.refID = 0

        expectHeld = 0

        if self.partyList:
            voiceCall = 1
            if byDefault == 2 or (extensions and extensions.has_key("chat")):  #or extensions ... in future
                voiceCall = 0
            for pt in self.partyList:
                if voiceCall and pt.Call.MediaType == MediaType.Voice and not pt.Held:
                    ProgrammError("DN %s cannot Make Call from the current state. Use HoldCall" % self.number)
                if not voiceCall and pt.Call.MediaType == MediaType.Chat and not pt.Held:
                    expectHeld = 1
                    break
        if expectHeld:
            self.HoldCall(byDefault=1)

        #fix by Vlad Gorelov 07/18/13 Specifics of MakeCall by Nailed-up agent is that SipPhone is in nailed-up DN contact needs to manually pickup the call
        if (self.line_type == "1") and not self.parked and not self.one_pcc_request and \
           (not len(self.partyList) or not self.partyList[0].inCall):
            self.SipPhone.AnswerCall(dest, waitEvents=False)
        self.one_pcc_request = 0
        # end of fix


        return self.commonMakeCall(dest, location=location, userData=userData, byDefault=byDefault)

    def AnswerCall(self, call=None, byDefault=0, extensions=None, reasons=None):

        # fix by Vlad Gorelov 08/01/13 AnswerCall is made for sip_model. All the changes associated with nailed-up functionality now are here
        if (self.line_type == "1"):
            if (not self.one_pcc_request):
                (cl,id) = self.SipPhone.AnswerCall()
                return cl
        self.one_pcc_request = 0
        # end of fix
        cl = DN.AnswerCall(self, call, byDefault, extensions, reasons)
        return cl

    def ReleaseCall(self, call=None, byDefault=0, extensions=None):
        #byDefault = 0, extensions = None - sequence different from other methods for compatibility
        """Method inherited from parent class because of required timeout when other party is in ringing state"""
        pt, call = self.getPartyFromCall((PartyState.Dialing, PartyState.Established, PartyState.Ringing), call)
        otherPartyRinging = False
        for otherPt in call.PartyList:  #checking if there is a ringing party
            if otherPt.State == PartyState.Ringing:
                otherPartyRinging = True
                break
        if otherPartyRinging and not byDefault and not self.tserver.requestTimout:
            time.sleep(0.2)
            DebugPrint("Debug: otherPartyRinging timeout 0.2")
        cl = DN.ReleaseCall(self, call, byDefault, extensions)
        if call and call.pendingQueue:
            call.pendingQueue.processRouteDestReleased(cl, self)
        return cl

    def roleOfConferenceMember(self, curRole):
        if curRole == PartyRole.Observer:
            return curRole
        return PartyRole.ConferenceMember


    def MonitorNextCall(self, agentDN, monitorType=MonitorNextCallType.MonitorOneCall, extensions=None):
        if not extensions:
            extensions = {"MonitorMode": "normal", "MonitorScope": "agent"}
        else:
            if not extensions.has_key("MonitorMode"):
                extensions["MonitorMode"] = "normal"
            if not extensions.has_key("MonitorScope"):
                extensions["MonitorScope"] = "agent"
            if not extensions.has_key("Location"):
                if self.tserver.location != agentDN.tserver.location:
                    ProgrammError("no location parameter in MonitorNextCall request")
        ## validating request parameters
        # if we are already supervising someone, then agentDN should be same as in current supervision subscription
        is_already_supervising = bool(self.supervisorFor)
        is_same_agent = self.supervisorFor == agentDN
        is_agent_in_call, is_intrusion_needed = agentDN.isInCall()
        is_in_call, _ = self.isInCall()
        if self.tserver.SwitchPolicyFeature():
            request = self.tserver.MonitorNextCall(self.number, agentDN.number, monitorType=monitorType,
                                                   extensions=extensions, send=0)
            self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, parties=self.partyList,
                                                               targetDN=agentDN, expectedAvailability=True)

        refID = self.tserver.MonitorNextCall(self.number, agentDN.number, monitorType=monitorType,
                                             extensions=extensions)
        self.printLog("\nMonitorNextCall(%s, agentDN = %s, monitorType = %s, extensions = %s, refID = %s)" % (
            self.number, agentDN.globalName, monitorType, extensions, refID))
        if is_already_supervising:
            if not is_same_agent or is_in_call:
                return self.eventError(addPrm={"ReferenceID": refID})

        #setting monitor session
        self.supervisorFor = agentDN
        self.monitorType = monitorType
        self.monitorMode = extensions["MonitorMode"]
        self.monitorMode_to_restore = extensions["MonitorMode"]
        self.monitorMode_in_request = extensions["MonitorMode"]
        self.monitorScope = extensions["MonitorScope"]
        self.monitorRequestedBy = "observer"
        agentDN.supervisedBy = self
        agentDN.monitorType = monitorType
        agentDN.monitorScope = extensions["MonitorScope"]
        agentDN.monitorMode = extensions["MonitorMode"]
        self.lastSuperviserFor = agentDN
        agentDN.monitorRequestedBy = "observer"

        if extensions.has_key("Location"):
            #agentDN.supervisedByLocation = extensions["Location"]
            self.supervisorForLocation = agentDN.tserver.location
            agentDN.observingRoutingPoint = agentDN.tserver.FindObservingRP()
            agentDN.observingRoutingPoint.observerRP = 1

        #end setting monitor session
        addPrm = {"ReferenceID": refID, "ThisDNRole": PartyRole.Observer, "OtherDN": agentDN,
                  "OtherDNRole": PartyRole.Destination, "MonitorNextCallType": monitorType}
        addPrm["Extensions"] = {}
        addPrm["Extensions"]["MonitorScope"] = self.monitorScope
        addPrm["Extensions"]["MonitorMode"] = self.monitorMode
        if extensions.has_key("Location"):  addPrm["Extensions"]["Location"] = extensions["Location"]
        self.eventMonitoringNextCall(addPrm=addPrm)
        addPrm = {"OtherDN": self, "OtherDNRole": PartyRole.Observer, "MonitorNextCallType": monitorType}
        addPrm["Extensions"] = {}
        addPrm["Extensions"]["MonitorScope"] = self.monitorScope
        addPrm["Extensions"]["MonitorMode"] = self.monitorMode
        agentDN.eventMonitoringNextCall(addPrm=addPrm)
        #if self.monitorScope == "agent":
        if is_intrusion_needed:  #agent already in call
            if self.tserver.monitor_consult_calls == "none":
                self.Intrude(agentDN, agentDN.partyList[0].Call)
            else:
                if len(agentDN.partyList) == 1:
                    self.Intrude(agentDN, agentDN.partyList[0].Call)
                elif len(agentDN.partyList) == 2:
                    for p in agentDN.partyList:
                        if not p.Held: self.Intrude(agentDN, p.Call)
                        #  self.Intrude(agentDN, agentDN.partyList[0].Call)
                        #else: #self.monitorScope == "call"
                        #  pass

    # Have no idea how to inherit it... easily. So copying fully from model_dn.py
    def SingleStepTransfer(self, dest, mainCall=None, location=None,
                           userData=None, extensions=None, reasons=None, byDefault=0):
        mainPt, mainCl = self.getPartyFromCall((PartyState.Established,), mainCall)
        realDest = dest
        if location:
            loc = ", location %s" % location
        else:
            loc = ""
        if not byDefault:
            time.sleep(self.tserver.requestTimout)
            if self.tserver.SwitchPolicyFeature():
                request = self.tserver.SingleStepTransfer(self.number, self.destFullNum(dest, location), mainCl.ConnID,
                                                          location=location, userData=None, extensions=extensions,
                                                          send=0)
                self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, party=mainPt, targetDN=dest,
                                                                   parties=self.partyList, expectedAvailability=True)

            refID = self.tserver.SingleStepTransfer(self.number, self.destFullNum(dest, location), mainCl.ConnID,
                                                    location=location, userData=None, extensions=extensions,
                                                    reasons=reasons)
            self.printLog("\nSingleStepTransfer (%s, dest = %s, connID = %s, refID = %s%s)" % (
                self.number, self.destFullNum(dest, location), ConnIDToStr(mainCl.ConnID), refID, loc))
        else:
            self.tserver.refID = 0
            self.printLog("\nSingleStepTransfer (%s, dest = %s, connID = %s, by Default%s)" % (
                self.number, self.destFullNum(dest, location), ConnIDToStr(mainCl.ConnID), loc))
        if location and location <> self.tserver.location:
            if self.postponedEventRemoteConnectionSuccess:
                if self.postponedEventRemoteConnectionSuccess == 3:
                    ev = self.mayBeEvent(EventName.RemoteConnectionSuccess, None, timeout=5)
                else:
                    self.eventRemoteConnectionSuccess(None)
            mainCl.ViaExtRouter = 1
            self.remoteConnectionSuccessRefID = refID
            self.postponedEventRemoteConnectionSuccess = 1

        addPrm = {"ByTransfer": 1, "CallState": CallState.Transferred, "ReferenceID": self.tserver.refID}
        if self.tserver.appVersion >= "7.0":
            addPrm["ThirdPartyDN"] = self.thirdPartyDNForSST(dest)
            addPrm["ThirdPartyDNRole"] = PartyRole.Destination
            if self.tserver.appVersion >= "7.2":
                addPrm["Cause"] = Cause.Cause1stepTransfer

        supervisorPtToLeave = None
        #if mainPt.supervisedByPt and self.monitorType == MonitorNextCallType.MonitorOneCall and self.monitorScope == "agent":
        if mainPt.supervisedByPt and self.monitorScope == "agent":
            supervisorPtToLeave = mainPt.supervisedByPt

        self.leaveCall(mainPt, notify=0, addPrm=addPrm, cause=Cause.Cause1stepTransfer)

        #saving old parties to generate PartyChanged on them later
        oldParties = copy.copy(mainCl.PartyList)

        mainCl.DNIS = None  #ER 52071 DNIS can change after sst
        extCall = 0
        if self.callToExtDN(dest):
            dest = self.trunk(self, dest)
            mainCl.external = 1
            extCall = 1
        addPrm = {"SSX": 1}

        ringPt = dest.ring(mainCl, CallState.Transferred, thisQueue=dest.inheritThisQueue(mainPt),
                           role=dest.inheritRole(mainPt), addPrm=addPrm)
        if not isinstance(ringPt, tuple):
            ringPt = (ringPt,)
        for pt in ringPt:
            pt.oneStepXferDestination = 1
        mainPtCnt = len(mainCl.PartyList)

        addPrm = {"CallState": CallState.Transferred,
                  "PreviousConnID": mainCl.ConnID,
                  "ThirdPartyDN": mainPt.DN.number,
                  "ThirdPartyDNLocation": mainPt.DN.tserver.location,
                  "ThirdPartyDNRole": PartyRole.TransferedBy}

        if mainCl.realPartiesCnt() == 2:
            if self.tserver.partyAfterSSxtoQueue == 2 and dest.type == AddressType.Queue:
                addPrm["OtherDN"] = "Trunk"
            elif self.tserver.partyAfterSSxtoQueue == 1 and dest.type == AddressType.Queue:
                destPt = mainCl.PartyList[mainPtCnt - 1]
                addPrm["OtherDN"] = destPt.DN
            else:
                addPrm["OtherDN"] = dest
                if GetOption("Partitioning"):
                    if location and location <> self.tserver.location:
                        addPrm["OtherDN"] = realDest

        for oldPt in oldParties:  # Parties from mainCl exept new party
            oldPt.DN.eventPartyChanged(oldPt, addPrm=addPrm)

        #after partychanged
        if supervisorPtToLeave:
            self.notifySupervisor(supervisorPtToLeave)

        if mainCl.Conference:
            # Change role of new party
            for pt in ringPt:
                pt.Role = self.roleOfConferenceMember(pt.Role)
                #mainCl.PartyList[mainPtCnt-1].Role = self.roleOfConferenceMember(mainCl.PartyList[mainPtCnt-1].Role)


        #autoanswer

        #TODO: autoAnswer handling for SST to HG
        if (not mainCl.toHg and hasattr(ringPt[0].DN, "getAutoAnswer") and
                ringPt[0].DN.getAutoAnswer(ringPt[0].Call)):
            ringPt[0].DN.autoEstablish(ringPt[0], addPrm={})
        ###########################################################################################
        #UK specific
        ###########################################################################################

        #if = 1 reset trunk after ssx to external and event partychanged expected on remote site
        if self.tserver.resetTrunkAfterSsxToExt:
            mainPtCnt = len(mainCl.PartyList)
            if mainPtCnt == 2:
                ringPt = mainCl.PartyList[1]
                if isinstance(ringPt.DN, Trunk0):
                    otherEndTrunkPt = ringPt.DN.otherEndParty()
                    if otherEndTrunkPt:
                        otherEndDestPt = OtherParty(ringPt.DN.otherEndCall(), otherEndTrunkPt)
                        if otherEndDestPt:
                            addPrm = {"ConnID": otherEndDestPt.Call.ConnID,
                                      "ThirdPartyDN": mainPt.DN.number,
                                      "ThirdPartyDNRole": PartyRole.TransferedBy,
                                      "CallState": CallState.Transferred}

                            otherEndTrunkPt.DN.trunkNumber = "Trunk"  # will be read from event PartyChanged and saved
                            otherEndDestPt.DN.mayBeEvent(EventName.PartyChanged, otherEndDestPt, addPrm=addPrm,
                                                         timeout=self.tserver.resetTrunkAfterSsxToExtTimeout)

        # if self.tserver.resetTrunkAfterSsxToInt = 1 reset trunk after ssx to internal device (for inbound calls only) and event partychanged expected on originator site
        if self.tserver.resetTrunkAfterSsxToInt:
            mainPtCnt = len(mainCl.PartyList)
            if mainPtCnt == 2:
                ringPt = mainCl.PartyList[0]
                if isinstance(ringPt.DN, Trunk0):
                    otherEndTrunkPt = ringPt.DN.otherEndParty()
                    if otherEndTrunkPt:
                        otherEndDestPt = OtherParty(ringPt.DN.otherEndCall(), otherEndTrunkPt)
                        if otherEndDestPt:
                            addPrm = {"ConnID": otherEndDestPt.Call.ConnID,
                                      "ThirdPartyDN": "Trunk",
                                      "ThirdPartyDNRole": PartyRole.TransferedBy,
                                      "CallState": CallState.Transferred,
                                      "OtherDNRole": "Trunk"}

                            otherEndTrunkPt.DN.trunkNumber = "Trunk"  # will be read from event PartyChanged and saved
                            otherEndDestPt.DN.mayBeEvent(EventName.PartyChanged, otherEndDestPt, addPrm=addPrm,
                                                         timeout=2)
        ###########################################################################################
        # if = 1 then QAART should expect networkReached evt after TSSx Transfer to external device
        if self.tserver.networkReached_on_ssx:
            if extCall:
                for callingParty in mainCl.PartyList[:mainPtCnt - 1]:  # Parties from mainCl exept new party
                    addPrm = {"OtherDN": "Trunk",
                              "CallState": CallState.Transferred}
                    callingParty.DN.mayBeEvent(EventName.NetworkReached, callingParty, timeout=2, addPrm=addPrm)
        ###########################################################################################

        self.specialObject.processTransferCompleted(mainPt, None, mainCl, None, [ringPt[0]])
        return mainCl

    # vgratsil - CPTT-119, 10-07-2014
    # copied from model_dn, inserted link establishment between conferencer and conferencee
    def CompleteConference(self, consCall=None, mainCall=None, tservOpr=1, merge=0, reasons=None, extensions=None,
                           byDefault=0):

        consPt, consCall, mainPt, mainCl, heldCallConnID, curCallConnID = self.resourcesForComplete(consCall, mainCall)
        consChatSession = None
        if consPt.chatSession: consChatSession = consPt.chatSession
        if tservOpr and not byDefault:
            time.sleep(self.tserver.requestTimout)
            if self.tserver.SwitchPolicyFeature():
                request = self.tserver.CompleteConference(self.number, heldCallConnID, curCallConnID, send = 0)
                self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn = self, party = mainPt, parties = self.partyList, expectedAvailability = True)
            refID = self.tserver.CompleteConference(self.number, heldCallConnID, curCallConnID, reasons = reasons, extensions = extensions)
            self.printLog("\nCompleteConference (%s, heldCallConnID = %s, curCallConnID = %s, refID = %s)" %
                   (self.number, ConnIDToStr(heldCallConnID), ConnIDToStr(curCallConnID), refID))
        if byDefault:
            self.tserver.refID = 0

        #--------------------

        addPrm = {"CallState": CallState.Conferenced, "ByConference": 1}
        addPrm2 ={"CallState": CallState.Conferenced, "ByConference": 1}
        i = 0

        heldPtObs = None
        if self.supervisedBy:
            for pt in mainCl.PartyList:
                if pt.Held and pt.Role == PartyRole.Observer:
                    heldPtObs = pt

        if not mainPt.Held:
            addPrm["ReferenceID"] = self.tserver.refID
            self.leaveCall(consPt, notify = 0, addPrm = addPrm, cause = Cause.CauseConference)
        else:

            while 1:
                pt = self.partyByEvent((mainPt, consPt), (EventName.Retrieved, EventName.Released))
                if pt:
                    if pt == mainPt:
                        if tservOpr or merge:
                            addPrm["ReferenceID"] = self.tserver.refID
                        mainPt.stateChanged(held = 0, cause = Cause.CauseConference)
                        self.eventRetrieved(mainPt, addPrm = addPrm)
                        self.leaveCall(consPt, notify = 0, addPrm = addPrm2, cause = Cause.CauseConference)
                        break
                    else:
                        if tservOpr or merge:
                            addPrm["ReferenceID"] = self.tserver.refID
                        self.leaveCall(consPt, notify = 0, addPrm = addPrm, cause = Cause.CauseConference)
                        mainPt.stateChanged(held = 0, cause = Cause.CauseConference)
                        self.eventRetrieved(mainPt, addPrm = addPrm2)
                        break
                else:
                    i = i + 1
                    if i > 2:
                        self.leaveCall(consPt, notify = 0, addPrm = addPrm, cause = Cause.CauseConference)
                        mainPt.stateChanged(held = 0, cause = Cause.CauseConference)
                        self.eventRetrieved(mainPt, addPrm = addPrm2)
                        break

            for brPt in mainPt.bridgedPts:
                brPt.stateChanged(held = 0, cause = Cause.CauseConference)
                brPt.DN.eventRetrieved(brPt, addPrm = addPrm2)

        #--------------------

        mainPtCnt = len(mainCl.PartyList)
        consPtCnt = len(consCall.PartyList)

        partyList = copy.copy(consCall.PartyList)
        self.specialObject.processConferenceCompleted(mainPt, mainCl, consCall, partyList)
        for newPt in partyList:
            newPt.moveToCall(mainCl, cause = Cause.CauseConference)
            newPt.DN.inheritThisQueue(mainPt, newPt)
            newPt.DN.inheritRole(mainPt, newPt)
            if newPt not in mainPt.conferencedParties:
                mainPt.conferencedParties.append(newPt)
            newPt.conferencedBy = mainPt
            if isinstance(newPt.DN, Trunk0):
                otherCall = newPt.DN.otherEndCall()
                for otherPt in otherCall.PartyList:
                    mainPt.set_link_by_property(otherPt, "conferencedBy")

        for pt in mainCl.PartyList:
            pt.Role = self.roleOfConferenceMember(pt.Role)

        #before partyadded?
        lastPtFromConsCall = mainCl.PartyList[-1]

        if isinstance(lastPtFromConsCall.DN, Trunk0) and consCall.UserData and consCall.UserData.has_key(self.tserver.UUIKey):
            if not mainCl.UserData or (mainCl.UserData and not mainCl.UserData.has_key(self.tserver.UUIKey)):
                anyPtFromMainCall = mainCl.PartyList[0]
                anyPtFromMainCall.DN.UpdateUserData( mainCl, userData = {self.tserver.UUIKey: consCall.UserData[self.tserver.UUIKey]}, tserverAction = 0l)

        consCall.MoveSecondaryConsultCallsToMainCall()


        addPrm = {"CallState":        CallState.Conferenced,
                  "ThirdPartyDN":     mainPt.DN.number,
                  "ThirdPartyDNLocation": mainPt.DN.tserver.location,
                  "ThirdPartyDNRole": PartyRole.AddedBy}

        if consPtCnt == 1:
            # For old parties in main call OtherDN is new party DN
            addPrm["OtherDN"]     = mainCl.PartyList[mainPtCnt].DN
            addPrm["OtherDNRole"] = PartyRole.NewParty

        if self.tserver.switchType and ((self.tserver.switchType == CfgSwitchType.CFGCiscoCM) \
           or (self.tserver.switchType == CfgSwitchType.CFGSIPSwitch) \
           or (self.tserver.switchType == CfgSwitchType.CFGNortelDMS100) \
           or (self.tserver.switchType in (CfgSwitchType.CFGLucentDefinityG3, CfgSwitchType.CFGAvayaTSAPI) and self.tserver.appVersion >= "7.2.099")):
            for oldPt in mainCl.PartyList[:mainPtCnt]: # Parties from mainCl
                for newPt in mainCl.PartyList[mainPtCnt:]:
                    if newPt.supervisorForPt and newPt.inCall and self.tserver.monitor_consult_calls != "none":
                        pass
                    else:
                        oldPt.DN.eventPartyAdded(oldPt, addPrm = addPrm)
                    #oldPt.DN.eventPartyAdded(oldPt, addPrm = addPrm)

        else:
            for oldPt in mainCl.PartyList[:mainPtCnt]: # Parties from mainCl
                oldPt.DN.eventPartyAdded(oldPt, addPrm = addPrm) #only 1 partyAdded event. if more than 1 party added, parties should be specified in extensions which we don't verify :(


        addPrm = {"CallState":        CallState.Conferenced,
                  "OtherDNNumber":          "optional",
                  "PreviousConnID":   consCall.ConnID,
                  "ThirdPartyDN":     mainPt.DN.number,
                  "ThirdPartyDNLocation": mainPt.DN.tserver.location,
                  "ThirdPartyDNRole": PartyRole.ConferencedBy}

        for newPt in mainCl.PartyList[mainPtCnt:]:   # Parties from consCall
            newPt.DN.eventPartyChanged(newPt, addPrm = addPrm)

        consCall = consCall.Close(cause = Cause.CauseConference, mainCall = mainCl)
        mainCl.Conference = 1
        if self.supervisedBy and self.tserver.monitor_consult_calls != "none" and self.supervisedBy.monitorScope == "agent":
            self.supervisedBy.monitorMode = self.supervisedBy.monitorMode_to_restore
            if self.supervisedBy.supervisorForLocation:
                self.partyList[0].supervisedByPt.muted = self.partyList[0].supervisedByPt.muted_to_restore
                self.partyList[0].supervisedByPt.DN.otherEndParty().muted = self.partyList[0].supervisedByPt.muted_to_restore
                self.supervisedBy.partyList[0].muted = self.supervisedBy.partyList[0].muted_to_restore
            if heldPtObs:
                heldPtObs.stateChanged(held = 0, cause = Cause.CauseConference)
                self.supervisedBy.eventRetrieved(heldPtObs)
        if consChatSession:
            mainPt.chatSession = consChatSession
        return mainCl

    def DeleteFromConference(self, dest, call = None, byDefault = 0):
        pt, call = self.getPartyFromCall(None, call)
        destPt, destCall = dest.getPartyFromCall(None, None)
        #if not destPt:
        #  ProgrammError("Destination dn %s is not a member of call " %dest)
        if destCall and self.tserver.CallReleaseTrackingFeature(): destCall.setCallControlParty(self.number)
        trunkPtToLeave = call.findTrunkPartyToOtherSite(dest)
        # vgratsil, CPTT-119 - 10-07-2014
        numberToDelete = self.destFullNum(dest, None)
        call_info_enabled = InTrue(getattr(self.tserver, "sip_enable_call_info", "false"))
        if call_info_enabled:
            numberToDelete = dest.number

        # this party is set as conferencedBy
        if not byDefault:
            time.sleep(self.tserver.requestTimout)
            refID = self.tserver.DeleteFromConference(self.number, numberToDelete, call.ConnID)
            self.printLog("DeleteFromConference(%s, dest = %s, connID = %s, refID = %s)" % (self.number, dest.number, ConnIDToStr(call.ConnID), refID))

        else:
            refID = 0
        addPrm = {"ThirdPartyDN":     self.number,
                  "ThirdPartyDNRole": PartyRole.DeletedBy,
                  "DeleteFromConference": 1}
        # vgratsil, CPTT-119 - 10-07-2014
        # actually it seems to be natural to propagate it for non-call-info-enabled cases, but i will keep
        # it for backward compatibility
        if trunkPtToLeave  and call_info_enabled: # switching to sip-enable-call-info mechanics
            if not pt.is_linked_by_property(destPt, "conferencedBy"):
                self.eventError(pt)
                return None
            else:
                return trunkPtToLeave.DN.leaveCall(trunkPtToLeave, addPrm=addPrm)
        if len(call.PartyList) == 2 or dest == self:
            if dest == self:
                addPrm["DeleteFromConference"] = 2 #need to flag if deleted self in conference for call release tracking
            addPrm["ReferenceID"] = refID
            return self.leaveCall(pt, addPrm = addPrm)
        else:
            return dest.leaveCall(destPt, addPrm = addPrm)

    def SingleStepConference(self, dest=None, call=None, location=None, \
                             userData=None, extensions=None, byDefault=0, bridgedTransfer=0, intrude=0, reasons=None):
        if not dest and not call:
            ProgrammError("Either destination or call should be specified in SSC request")
        if location:
            loc = ", location %s" % location
        else:
            loc = ""
        if dest and not intrude:
            mainPt, mainCl = self.getPartyFromCall((PartyState.Established, PartyState.Dialing, PartyState.Ringing),
                                                   call)
            destNum = self.destFullNum(dest, location)
        else:  #no dest or intrude
            if intrude:  # dest should be agent to supervise
                mainPt, mainCl = dest.getPartyFromCall((), call)
                destNum = self.destFullNum(dest, location)
            else:
                mainCl = call
                dest = self
                destNum = ""
                mainPt = None

        refID = 0
        if self.tserver.appVersion >= "8.0.4":
            userData = userData
        else:
            userData = None
        if not byDefault:
            time.sleep(self.tserver.requestTimout)
            if extensions:
                exts = ", extensions = %s" % extensions
            else:
                exts = ""
            if self.tserver.SwitchPolicyFeature():
                request = self.tserver.SingleStepConference(self.number, destNum, mainCl.ConnID, location=location,
                                                            userData=userData, extensions=extensions, send=0)
                self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, party=mainPt,
                                                                   parties=self.partyList, expectedAvailability=True)

            refID = self.tserver.SingleStepConference(self.number, destNum, mainCl.ConnID, location=location,
                                                      userData=userData, extensions=extensions, reasons=reasons)
            self.printLog("\nSingleStepConference (%s, dest = %s, connID = %s, refID = %s%s%s)" % (
                self.number, destNum, ConnIDToStr(mainCl.ConnID), refID, exts, loc))
        else:
            self.printLog("\nSingleStepConference (%s, dest = %s, connID = %s, by Default%s)" % (
                self.number, destNum, ConnIDToStr(mainCl.ConnID), loc))
        if extensions and extensions.has_key("AssistMode") and extensions["AssistMode"] in ("coach", "connect"):
            #setting monitor session
            dest.supervisorFor = self
            dest.monitorType = MonitorNextCallType.MonitorOneCall
            dest.monitorMode = extensions["AssistMode"]
            dest.monitorScope = "call"
            dest.monitorRequestedBy = "agent"
            self.supervisedBy = dest
            self.monitorType = MonitorNextCallType.MonitorOneCall
            self.monitorMode = extensions["AssistMode"]
            self.monitorScope = "call"
            self.monitorRequestedBy = "agent"
            dest.lastSuperviserFor = self


            #end setting monitor session
            return dest.Intrude(self, mainCl, bySSC=1)
        if intrude:
            self.supervisorFor = dest
            self.monitorType = MonitorNextCallType.MonitorOneCall
            self.monitorScope = "agent"
            dest.supervisedBy = self
            dest.monitorType = MonitorNextCallType.MonitorOneCall
            dest.monitorScope = "agent"
            self.lastSuperviserFor = dest
            return self.Intrude(dest, mainCl, bySSC=2)
        if location and location <> self.tserver.location:
            mainCl.ViaExtRouter = 1
            self.remoteConnectionSuccessRefID = refID
            self.postponedEventRemoteConnectionSuccess = 1
        mainCl.DNIS = None  #ER 52071 DNIS can change after sst
        mainPtCnt = len(mainCl.PartyList)

        if self.callToExtDN(dest):
            dest = self.trunk(self, dest)
            mainCl.external = 1
        addPrm = {}
        if dest == self:
            addPrm["ReferenceID"] = refID
        if bridgedTransfer:
            mainPt.bridger = 1
        if not bridgedTransfer:
            addPrm["ThisDNRole"] = PartyRole.ConferenceMember

        if userData:
            self.UpdateUserData(userData=userData, tserverAction=0)
        ringPt = dest.ring(mainCl, CallState.Ok, addPrm=addPrm)
        mainPt.set_link_by_property(ringPt, "conferencedBy")
        if extensions and extensions.has_key("AssistMode"):
            mainPt.supervisedByPt = ringPt
            ringPt.supervisorForPt = mainPt

        if not bridgedTransfer:
            for pt in mainCl.PartyList:
                pt.Role = self.roleOfConferenceMember(pt.Role)

            mainCl.Conference = 1
            if not mainCl.toHg and ringPt.DN.autoAnswerOnSSC:
                ringPt.DN.establish(ringPt, addPrm={"CallState": CallState.Conferenced})

            addPrm = {"CallState": CallState.Conferenced}
            if not byDefault:
                addPrm["ThirdPartyDN"] = self.number
                addPrm["ThirdPartyDNLocation"] = self.tserver.location
                addPrm["ThirdPartyDNRole"] = PartyRole.AddedBy

            addPrm["OtherDN"] = mainCl.PartyList[mainPtCnt].DN
            addPrm["OtherDNRole"] = PartyRole.NewParty

            addPrm2 = copy.copy(addPrm)
            if dest != self:
                addPrm2["ReferenceID"] = refID

            for oldPt in mainCl.PartyList[:mainPtCnt]:  # Parties from mainCl
                if oldPt.DN == self:
                    oldPt.DN.eventPartyAdded(oldPt, addPrm=addPrm2)
                else:
                    oldPt.DN.eventPartyAdded(oldPt, addPrm=addPrm)

        return mainCl


    def RedirectCall(self, dest, call=None, callState=CallState.Redirected, reasons=None, extensions=None, byDefault=0):
        ringPt, call = self.getPartyFromCall((PartyState.Ringing, PartyState.Established), call)
        # saving link to transit it to next party
        partyToRelink = ringPt.get_link_by_property_reverse("conferencedBy")

        if not byDefault:
            time.sleep(self.tserver.requestTimout)
            if self.tserver.SwitchPolicyFeature():
                request = self.tserver.RedirectCall(self.number, self.destFullNum(dest), call.ConnID, send=0)
                self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, party=ringPt,
                                                                   parties=self.partyList, expectedAvailability=True)

            refID = self.tserver.RedirectCall(self.number, self.destFullNum(dest), call.ConnID, reasons=reasons,
                                              extensions=extensions)
            self.printLog("\nRedirectCall (%s, dest = %s, call.ConnID = %s, refID = %s)" %
                          (self.number, self.destFullNum(dest), ConnIDToStr(call.ConnID), refID))
        else:
            refID = 0
            self.printLog("\nRedirectCall (%s, dest = %s, call.ConnID = %s, by Default)" %
                          (self.number, self.destFullNum(dest), ConnIDToStr(call.ConnID)))
        addPrm = {"CallState": callState, "ReferenceID": refID}
        if self.tserver.appVersion >= "7.2":
            addPrm["ThirdPartyDN"] = self.destFullNum(dest)
            if call.realPartiesCnt() > 2:
                addPrm["ThirdPartyDNRole"] = PartyRole.ConferenceMember
            else:
                addPrm["ThirdPartyDNRole"] = PartyRole.Destination
        addPrm["IgnoreThirdPartyDNParInPartyDeleted"] = 1
        if byDefault:
            self.tserver.refID = 0
        if self.tserver.appVersion >= "7.6":
            self.leaveCall(ringPt, notify=0, abandPermited=0, addPrm=addPrm)
        else:  #7.5
            self.leaveCall(ringPt, notify=2, abandPermited=0, addPrm=addPrm)
        if self.callToExtDN(dest):
            dest = self.trunk(self, dest)
            call.external = 1

        resPt = dest.ring(call, callState, thisQueue=dest.inheritThisQueue(ringPt), role=dest.inheritRole(ringPt))
        resPt = resPt if isinstance(resPt, tuple) else (resPt, )
        partyToRelink.set_link_by_property(resPt, "conferencedBy")

        if self.tserver.appVersion < "7.6":  # 7.5

            if resPt and len(resPt.Call.PartyList) > 2:
                for pt in resPt.Call.PartyList:
                    pt.Role = self.roleOfConferenceMember(pt.Role)
                    if not (pt is resPt):
                        addPrm = {"CallState": CallState.Conferenced,
                                  "ThirdPartyDN": self.number,
                                  "ThirdPartyDNLocation": self.tserver.location,
                                  "ThirdPartyDNRole": PartyRole.AddedBy,
                                  "OtherDN": resPt.DN,
                                  "OtherDNRole": PartyRole.NewParty}
                        pt.DN.eventPartyAdded(pt, addPrm=addPrm)

        if len(resPt):
            return resPt[0].Call


    def CancelMonitoring(self, agentDN, extensions=None):
        """Cancel monitoring session. Does not work if session is cancelled during the call
          Parameters:
            agentDN    - DN object
            extensions - dict
          Return       - None
        """
        if self.tserver.SwitchPolicyFeature():
            request = self.tserver.CancelMonitoring(self.number, agentDN.number, send=0)
            self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, parties=self.partyList,
                                                               targetDN=agentDN, expectedAvailability=True)

        refID = self.tserver.CancelMonitoring(self.number, agentDN.number, extensions=extensions)
        self.printLog("\nCancelMonitoring(%s, agentDN = %s, extensions = %s, refID = %s)" % (
            self.number, agentDN.globalName, extensions, refID))
        if not self.supervisorFor:
            return self.eventError(addPrm={"ReferenceID": refID})
        agentDN = self.supervisorFor
        addPrm = {"ReferenceID": refID, "ThisDNRole": PartyRole.Observer, "OtherDN": agentDN,
                  "OtherDNRole": PartyRole.Destination, "MonitorNextCallType": self.monitorType}
        self.eventMonitoringCancelled(addPrm=addPrm)
        addPrm = {"OtherDN": self, "OtherDNRole": PartyRole.Observer, "MonitorNextCallType": self.monitorType}
        agentDN.eventMonitoringCancelled(addPrm=addPrm)
        self.endMonitoringSession()
        agentDN.endMonitoringSession()


    def completeMakeCallOutbound(self, party, dest, addPrm):
        DN.completeMakeCallOutbound(self, party, dest, addPrm)
        if party.Call.Scope == CallScope.Outbound:
            if isinstance(dest.dest, SIP_Queue):
                self.mayBeEstablish(party)

    def ReconnectCall(self, heldCall=None, curCall=None, extensions=None, reasons=None, byDefault=0):
        cl = DN.ReconnectCall(self, heldCall, curCall, extensions, reasons, byDefault)
        #supervisor should join the main call
        if self.supervisedBy and self.tserver.monitor_consult_calls != "none":
            if self.supervisedBy.monitorScope == "agent" and not cl.ConsultType:
                self.supervisedBy.monitorMode = self.supervisedBy.monitorMode_to_restore
                if self.supervisedBy.supervisorForLocation:
                    self.partyList[0].supervisedByPt.muted = self.partyList[0].supervisedByPt.muted_to_restore
                    self.partyList[0].supervisedByPt.DN.otherEndParty().muted = self.partyList[0].supervisedByPt.muted_to_restore
                    self.supervisedBy.partyList[0].muted = self.supervisedBy.partyList[0].muted_to_restore
            if len(self.supervisedBy.partyList) == 0:
                print "ReconnectCall - Intrude", cl
                self.supervisedBy.Intrude(self, cl)
        return cl

    def AlternateCall(self, heldCall=None, curCall=None, extensions=None, reasons=None, byDefault=0):
        curPt, cCall = self.getPartyFromCall(None, curCall)
        heldPt, hCall = self.getHeldPartyFromCall(heldCall, defaultPartyNum=-2)
        heldPtObs = None
        ptObs = None
        heldCallPtObs = None
        if not hCall or not cCall:
            heldPt, hCall = self.findHeldParty()
            curPt, cCall = self.findNotHeldParty()
        if self.supervisedBy:
            for pt in cCall.PartyList:
                if not pt.Held and pt.Role == PartyRole.Observer:
                    ptObs = pt
            for pt in hCall.PartyList:
                if pt.Held and pt.Role == PartyRole.Observer:
                    heldPtObs = pt
                elif pt.Role == PartyRole.Observer:
                    heldCallPtObs = pt
        cl = DN.AlternateCall(self, heldCall, curCall, extensions, reasons, byDefault)
        #If an agent requests assistance in a consult call and alternates back to the main call the supervisor will hear "hold" music and will not alternate or reconnect back to the main call (just as in 8.0, and the same as for monitor scope "call").
        if self.tserver.monitor_consult_calls != "none":
            if self.supervisedBy and self.supervisedBy.monitorScope == "agent" and not cl.ConsultType:
                self.supervisedBy.monitorMode = self.supervisedBy.monitorMode_to_restore
                if self.supervisedBy.supervisorForLocation:
                    self.partyList[0].supervisedByPt.muted = self.partyList[0].supervisedByPt.muted_to_restore
                    self.partyList[0].supervisedByPt.DN.otherEndParty().muted = self.partyList[0].supervisedByPt.muted_to_restore
                    self.supervisedBy.partyList[0].muted = self.supervisedBy.partyList[0].muted_to_restore
            if self.supervisedBy and ptObs and self.supervisedBy.monitorScope != "call":
                ptObs.stateChanged(held=1)  # to receive Held state
                self.supervisedBy.eventHeld(ptObs, addPrm={"ReferenceID": 0})
            if self.supervisedBy and heldPtObs and not self.supervisedBy.supervisor_action_hold:
                heldPtObs.stateChanged(held=0)
                self.supervisedBy.eventRetrieved(heldPtObs)
            elif self.supervisedBy and not heldPtObs and not self.supervisedBy.supervisor_action_hold \
                    and self.supervisedBy.monitorScope != "call" and not heldCallPtObs and self.tserver.monitor_consult_calls != "dest":
                print "supervisor should join the main call"
                self.supervisedBy.Intrude(self, hCall)
                self.supervisedBy.AnswerCall(byDefault=1)
        return cl

    def HoldCall(self, call=None, extensions=None, reasons=None, byDefault=0):
        if self.supervisorFor:
            self.supervisor_action_hold = 1  #on hold by seperv
        return DN.HoldCall(self, call, extensions, reasons, byDefault)

    def RetrieveCall(self, call=None, extensions=None, reasons=None, byDefault=0):
        if self.supervisorFor and self.supervisor_action_hold:
            self.supervisor_action_hold = None  #was on hold by seperv
        heldPt, cl = self.getHeldPartyFromCall(call)
        ptObs = None
        if self.supervisedBy:
            for pt in cl.PartyList:
                if pt.Held and pt.Role == PartyRole.Observer:
                    ptObs = pt
        call = DN.RetrieveCall(self, call, extensions, reasons, byDefault)

        if self.supervisedBy:
            if self.tserver.monitor_consult_calls != "none":
                if self.supervisedBy.monitorScope == "agent" and not call.ConsultType:
                    self.supervisedBy.monitorMode = self.supervisedBy.monitorMode_to_restore
                    if self.supervisedBy.supervisorForLocation:
                        self.partyList[0].supervisedByPt.muted = self.partyList[0].supervisedByPt.muted_to_restore
                        self.partyList[0].supervisedByPt.DN.otherEndParty().muted = self.partyList[0].supervisedByPt.muted_to_restore
                        self.supervisedBy.partyList[0].muted = self.supervisedBy.partyList[0].muted_to_restore
            if ptObs:
                ptObs.stateChanged(held=0)
                self.supervisedBy.eventRetrieved(ptObs)
        return call

    def establish(self, party, addPrm=None):
        if party.State != PartyState.Dialing:
            cl = party.Call
            if cl and cl.pendingQueue:
                #and isinstance(cl.pendingQueue, SIP_RouteDN):
                cl.pendingQueue.distrCallToDestination(cl, self)
        if not isinstance(self.SipPhone, Any):
            idx = -1
            if party.DN.partyList[-1] <> party:  # this is not the last party
                idx = party.DN.partyList.index(party)
            if not party.Held:
                self.SipPhone.WaitPartyState('connected', timeout=15, index=idx)
            else:
                self.SipPhone.WaitPartyState('held', timeout=2, index=idx, optional=1)
        if self.supervisorFor and len(self.partyList) == 1:
            if not addPrm: addprm = {}
            dest = self.supervisorFor
            if not self.supervisorForLocation:
                destPt, destCl = dest.getPartyFromCall((PartyState.Established,), party.Call)
                otherPt = dest.otherPartyFromCallExcept(destPt, partiesToExclude=[party], cl=destCl)
            else:
                destCl = None
                destPt, destCl = dest.getPartyFromCall((PartyState.Established,), destCl)
                otherPt = self.otherPartyFromCall(destPt)
                if isinstance(otherPt.DN, Trunk0):
                    otherEndTrunkPt = otherPt.DN.otherEndParty()
                    if otherEndTrunkPt:
                        otherPt = OtherParty(otherPt.DN.otherEndCall(), otherEndTrunkPt)
                addPrm["ThisDNRole"] = PartyRole.Observer
            if otherPt:
                addPrm["OtherDN"] = otherPt.DN
                addPrm["OtherDNRole"] = otherPt.Role
            addPrm["CallState"] = CallState.Bridged
            #addPrm["Extensions"] = {"MonitorMode": self.monitorMode, "MonitorScope": self.monitorScope}
            if self.monitorMode == "coach":
                party.muted = 2
            elif self.monitorMode == "connect":
                party.muted = 0
            else:
                party.muted = 1
            #print party
            #print party.muted
            party.muted_to_restore = party.muted
            if self.supervisorForLocation:
                otherParty = self.otherPartyFromCall(party)
                if otherParty and isinstance(otherParty.DN, Trunk0):
                    otherParty.muted = party.muted
                    otherParty.muted_to_restore = party.muted
                    otherEndTrunkPt = otherParty.DN.otherEndParty()
                    otherEndTrunkPt.muted = party.muted
                    otherEndTrunkPt.muted_to_restore = party.muted
                    party.supervisorForPt = otherEndTrunkPt.supervisorForPt
                    otherParty.supervisorForPt = otherEndTrunkPt.supervisorForPt
        DN.establish(self, party, addPrm)

        if self.supervisedBy:
            if not party.Call.ConsultType and not self.supervisedBy.partyList:  #
                self.supervisedBy.Intrude(self, party.Call)
                return
            elif not self.supervisedBy.partyList:  #supervisor is free
                if not party.Call.ConsultType and party.Role == PartyRole.Destination:  #agent under supervision receives a call
                    self.supervisedBy.Intrude(self, party.Call)  #was to here
                elif party.Call.ConsultType and party.DN.tserver.monitor_consult_calls in ("all", "dest", "true", "1") \
                    and party.Role == PartyRole.Destination:
                    #when supervisor is free and agent under supervision receives a consult call
                    mainCl = party.Call.MainCall
                    if mainCl:
                        ptCntM = len(party.Call.MainCall.PartyList)
                    else:
                        ptCntM = 0
                    if party.DN.monitorScope == "call":
                        for ptM in party.Call.MainCall.PartyList[:ptCntM]:
                            if ptM.DN.supervisorFor:
                                #we already have SU for main call, another SU will not join consult
                                #"when a consult call to a monitored destination is made by a monitored agent then the supervisor monitoring the agent
                                #(not the destination) will join the call"
                                print "2 SUs, scope call"
                                return
                    elif party.DN.monitorScope == "agent":
                        for ptM in party.Call.MainCall.PartyList[:ptCntM]:
                            if ptM.DN.supervisorFor:
                                print "2 SUs, scope agent", ptM.DN.supervisorFor
                                addPrm["ThisDNRole"] = PartyRole.Observer
                                addPrm["ReferenceID"] = 0
                                ptM.DN.eventHeld(ptM, addPrm=addPrm)
                                ptM.stateChanged(held=1)
                                ptM.DN.Intrude(ptM.DN.supervisorFor, call=party.Call)
                                ptM.DN.AnswerCall(byDefault=1)
                                return
                    self.supervisedBy.Intrude(self, party.Call)
        if party.Call and party.DN.tserver.monitor_consult_calls in ("all", "orig", "true", "1"):
            #agent under supervision sends a call
            ptCnt = len(party.Call.PartyList)
            len1 = len(self.partyList)
            for pt in party.Call.PartyList[:ptCnt]:
                if pt.DN.supervisedBy and not self.supervisedBy and not self.supervisorFor:
                    scope = pt.DN.monitorScope
                    if pt.State == PartyState.Ringing or (scope == "call" and party.Call.ConsultType) or pt.DN.supervisedBy.supervisorForLocation:
                        if pt.DN.supervisedBy.supervisorForLocation and len(pt.DN.supervisedBy.partyList):
                            if pt.DN.supervisedBy.monitorMode_in_request == "coach":
                                pt.DN.supervisedBy.partyList[0].muted = MuteState.Coach
                            elif pt.DN.supervisedBy.monitorMode_in_request == "connect":
                                pt.DN.supervisedBy.partyList[0].muted = MuteState.Connect
                            else:
                                pt.DN.supervisedBy.partyList[0].muted = MuteState.Muted
                            pt.DN.supervisedBy.monitorMode = pt.DN.supervisedBy.monitorMode_in_request
                        return
                    ptCnt1 = len(pt.DN.supervisedBy.partyList)
                    ptCnt2 = len(pt.DN.partyList)
                    supervPt = None
                    if ptCnt2 < 2: return
                    if ptCnt1 == 1: supervPt = pt.DN.supervisedBy.partyList[0]
                    for pt2 in pt.DN.partyList[:ptCnt2]:
                        if pt2.Held and pt.DN.supervisedBy.partyList:
                            addPrm["ThisDNRole"] = PartyRole.Observer
                            addPrm["ReferenceID"] = 0
                            pt.DN.supervisedBy.eventHeld(supervPt, addPrm=addPrm)
                            supervPt.stateChanged(held=1)
                    if not pt.DN.supervisedBy.partyList:
                        su = "free"
                    else:
                        su = "busy"
                    print "su=", pt.DN.supervisedBy.partyList, "su=", su
                    pt.DN.supervisedBy.Intrude(pt.DN, call=party.Call) #was ohne dest
                    if pt.DN.supervisedBy.monitorMode == "coach":
                        pt.supervisedByPt.muted = MuteState.Coach
                    elif pt.DN.supervisedBy.monitorMode == "connect":
                        pt.supervisedByPt.muted = MuteState.Connect
                    else:
                        pt.supervisedByPt.muted = MuteState.Muted
                    if pt.State != PartyState.Ringing and su == "busy" and not pt.DN.supervisedBy.autoAnswerOnSSC:  #if len1 > 1:#agent init cons call
                        pt.DN.supervisedBy.AnswerCall(byDefault=1)


    def ring(self, call, callState=CallState.Ok, thisQueue=None, role=None, addPrm=None):
        if not isinstance(self.SipPhone, Any):
            if not (self.supervisorFor and len(
                    self.partyList) > 0) and not self.forwardDest and not self.parked:  #fix by Vlad Gorelov 07/10/13 parameter parked added
                self.SipPhone.WaitPartyState('alerting', timeout=15)
            if self.parked:  #fix by Vlad Gorelov 07/11/13
                self.SipPhone.WaitPartyState('connected', timeout=15)
        cl = DN.ring(self, call, callState, thisQueue, role, addPrm)
        if self.number == "gcti::park":
            pt = self.partyList[0]
            otherPt = pt.Call.PartyList[0]
            otherPt.DN.eventEstablished(otherPt)
            pt.DN.completeMakeCall(pt, otherPt.DN)
            otherPt.DN.leaveCall(otherPt)
        return cl


    def leaveCall(self, party, notify=1, abandPermited=1, addPrm=None, cause=Cause.CauseEMPTY):
        call = party.Call
        notifyToUse = notify
        abandPermitedToUse = abandPermited
        dialpark = False
        if call.pendingQueue:
            if not call.pendingQueue.partyList or call.pendingQueue.partyList[0].Role != PartyRole.ConferenceMember:
                notifyToUse = 0
                abandPermitedToUse = 0
        if party.State == PartyState.Dialing:
            self.mayBeEvent(EventName.DestinationBusy, None, timeout=1)
            if self.line_type == "1":
                otherPt = self.otherRealPartyFromCall(party, call)
                if otherPt.DN.number == "gcti::park":
                    otherPt.removeFromCall()
                    dialpark = True
                if party.Call.ConsultType or (otherPt and (not isinstance(otherPt.DN, SIP_RouteDN)
                   or otherPt.treatmentApplied)):
                    dialpark = True
        cl = DN.leaveCall(self, party, notifyToUse, abandPermitedToUse, addPrm, cause)
        if not isinstance(self.SipPhone, Any) and len(party.DN.partyList) == 0:
            #fix by Vlad Gorelov 10/31/13. if DN is not nailed-up anymore or dropped DN is parked no more
            if self.line_type == "1":
                if party.State == PartyState.Established or dialpark:
                    if (not (self.tserver.drop_nailedup_on_logout in ("1", "true"))) or (
                        (self.agentLogin) and (self.agentLogin.state != AgentState.Logout)):
                        self.parked = 1
            else:
                self.parked = 0
            if self.one_pcc_request:  # if we do 1pcc Release nailedup  DN is unparked
                self.parked = 0
            if not self.supervisorFor and not self.parked:
                self.SipPhone.WaitPartyState(None, timeout=15)
            if self.parked:
                self.SipPhone.WaitPartyState('connected', timeout=15)
            self.one_pcc_request = 0

            #end of fix
        return cl


    def eventHeld(self, party, printEventHdr=1, addPrm=None):

        if not isinstance(self.SipPhone, Any):
            idx = -1
            if party.DN.partyList[-1] <> party:  # this is not the last party
                idx = party.DN.partyList.index(party)
            self.SipPhone.WaitPartyState('held', timeout=2, optional=1, index=idx)  # sometimes also non stable state
        return DN.eventHeld(self, party, printEventHdr, addPrm)

    def eventRetrieved(self, party, printEventHdr=1, addPrm=None):
        if not isinstance(self.SipPhone, Any):
            idx = -1
            if party.DN.partyList[-1] <> party:  # this is not the last party
                idx = party.DN.partyList.index(party)
            self.SipPhone.WaitPartyState('connected', timeout=2, optional=1)
        return DN.eventRetrieved(self, party, printEventHdr, addPrm)


    def eventPartyDeleted(self, party, printEventHdr=1, addPrm=None):
        if party.inCall:
            if not self.supervisorForLocation:
                ev = self.handleEvent("PartyDeleted", party, CallEvent3, printEventHdr, addPrm)
            else:
                ev = None
            if party.postponedEventMonitoringCancelledAddPrm:
                party.DN.eventMonitoringCancelled(party, addPrm=party.postponedEventMonitoringCancelledAddPrm)
                party.DN.endMonitoringSession()
            return ev

    def eventReleased(self, party, printEventHdr=1, addPrm=None):
        if party.inCall:
            if party.postponedEventMonitoringCancelledAddPrm:
                party.postponedEventMonitoringCancelledAddPrm = None
                self.waitEventSet(party, [EventName.Released, EventName.MonitoringCancelled],
                                  addPrm=[addPrm, party.postponedEventMonitoringCancelledAddPrm])
                party.DN.endMonitoringSession()
            else:
                return self.handleEvent("Released", party, CallEvent3 + ("Cause",), printEventHdr, addPrm)


    def lastPartyOnDN(self, party, cause, onHook, agentEvent, addPrm, abandPermited=1):
        callMonEv = None
        callMonEvAddPrm = None

        if (party.supervisorForPt and self.monitorType == MonitorNextCallType.MonitorOneCall):

            callMonEv = EventName.MonitoringCancelled
            callMonEvAddPrm = self.callMonitorEventAddPrm()

            agentPt = party.supervisorForPt

            agentCallMonEvAddPrm = agentPt.DN.callMonitorEventAddPrm()
            if self.monitorRequestedBy == "observer":
                if self.monitorScope == "agent":
                    if agentPt.inCall:
                        agentPt.postponedEventMonitoringCancelledAddPrm = agentCallMonEvAddPrm
                elif self.monitorScope == "call":
                    if agentPt.inCall:
                        agentPt.postponedEventMonitoringCancelledAddPrm = agentCallMonEvAddPrm
                    else:
                        if party.DN.supervisorForLocation and not agentPt.DN.supervisedBy:
                            pass
                        else:
                            agentPt.DN.eventMonitoringCancelled(addPrm=agentCallMonEvAddPrm)
                            agentPt.DN.endMonitoringSession()
            elif self.monitorRequestedBy == "agent":  #requested by agent
                agentPt.DN.endMonitoringSession()
                callMonEv = None
                callMonEvAddPrm = None
            else:
                assert (0)

        elif self.supervisorFor and self.monitorType == MonitorNextCallType.MonitorAllCalls:
            self.monitorMode = self. monitorMode_in_request

        elif self.supervisedBy and self.monitorType == MonitorNextCallType.MonitorOneCall and self.monitorScope == "agent":
            if self.monitorRequestedBy == "agent":
                agentPt.DN.endMonitoringSession()
                callMonEv = None
                callMonEvAddPrm = None
            elif self.monitorRequestedBy == "observer":
                #if cause not in (Cause.CauseTransfer, Cause.Cause1stepTransfer):
                callMonEv = EventName.MonitoringCancelled
                callMonEvAddPrm = self.callMonitorEventAddPrm()
                if party.postponedEventMonitoringCancelledAddPrm:
                    party.postponedEventMonitoringCancelledAddPrm = None
            else:
                assert (0)
        else:
            if party.postponedEventMonitoringCancelledAddPrm:
                callMonEv = EventName.MonitoringCancelled
                callMonEvAddPrm = party.postponedEventMonitoringCancelledAddPrm
                party.postponedEventMonitoringCancelledAddPrm = None
            else:
                callMonEv = None
                callMonEvAddPrm = None

        if party.State is PartyState.Ringing:
            if party.inCall:
                if abandPermited:
                    if not self.supervisorFor:
                        callMonEv = None
                        callMonEvAddPrm = None
                    self.waitEventSet(party, [EventName.Abandoned, agentEvent, callMonEv],
                                      addPrm=[addPrm, {"ReferenceID": 0}, callMonEvAddPrm])

                else:
                    self.waitEventSet(party, [onHook, EventName.Released, agentEvent, callMonEv],
                                      addPrm=[addPrm, addPrm, {"RefereceID": 0}, callMonEvAddPrm])
        else:
            #self.mayBeEvent(EventName.DestinationBusy, None, timeout = 1)   #fix by Vlad Gorelov 02/04/14
            self.waitEventSet(party, [onHook, EventName.Released, agentEvent, callMonEv],
                              addPrm=[addPrm, addPrm, {"RefereceID": 0}, callMonEvAddPrm])

        if callMonEv or self.monitorRequestedBy == "agent":
            self.endMonitoringSession()

    def notifySupervisor(self, supervisorPtToLeave, addPrm={}, byRelease=1):
        if supervisorPtToLeave and supervisorPtToLeave.inCall:  # and supervisorPtToLeave.DN.monitorScope == "agent" and supervisorPtToLeave.DN.monitorMode <> "connect":
            #addPrm["doNotChangeOtherDN"] = 1
            #supervisorPtToLeave.DN.leaveCall(supervisorPtToLeave, addPrm = addPrm, notify = 1)
            if isinstance(supervisorPtToLeave.DN, Trunk0):
                superv_DN = self.supervisedBy
            else:
                superv_DN = supervisorPtToLeave.DN
            notify = 1
            if len(supervisorPtToLeave.DN.partyList) > 1:
                notify = 0
            elif superv_DN and superv_DN.monitorScope == "agent" and len(supervisorPtToLeave.Call.PartyList) > 2:  #55
                notify = 1  #release from conf - SU agent left, su+ag2+customer
            elif superv_DN and superv_DN.monitorScope == "agent" and supervisorPtToLeave.DN.tserver.monitor_consult_calls != "none":  # look
                notify = 0
            supervisorPtToLeave.DN.leaveCall(supervisorPtToLeave, addPrm=addPrm, notify=notify)

    def CompleteTransfer(self, consCall=None, mainCall=None, tservOpr=1, dest=None, merge=0, byDefault=0):
        consPt, cnsCall, mainPt, mainCl, heldCallConnID, curCallConnID = self.resourcesForComplete(consCall, mainCall)

        supervisorPtToLeave = None
        #if mainPt.supervisedByPt and self.monitorType == MonitorNextCallType.MonitorOneCall \
        if mainPt.supervisedByPt \
                and self.monitorScope == "agent":
            supervisorPtToLeave = mainPt.supervisedByPt

        #if InTrue(GetOption("TrunkOptimization")):
        otherEndMainCall = None
        otherEndConsCall = None
        if len(mainCl.PartyList) == 2 and len(cnsCall.PartyList) == 2:
            otherPtFromMainCall = self.otherPartyFromCall(mainPt)
            otherPtFromConsCall = self.otherPartyFromCall(consPt)
            if isinstance(otherPtFromMainCall.DN, Trunk0) and isinstance(otherPtFromConsCall.DN, Trunk0):
                otherEndPtMain = otherPtFromMainCall.DN.otherEndParty()
                otherEndPtCons = otherPtFromConsCall.DN.otherEndParty()

                otherEndMainCall = otherPtFromMainCall.DN.otherEndCall()
                otherEndConsCall = otherPtFromConsCall.DN.otherEndCall()

        cl = DN.CompleteTransfer(self, consCall, mainCall, tservOpr, dest, merge, byDefault=byDefault)
        #partychanged first
        if supervisorPtToLeave:
            self.notifySupervisor(supervisorPtToLeave)

        if self.supervisedBy and self.tserver.monitor_consult_calls != "none" and self.supervisedBy.monitorScope == "agent":
            self.supervisedBy.monitorMode = self.supervisedBy.monitorMode_to_restore
            if self.supervisedBy.supervisorForLocation:
                self.partyList[0].supervisedByPt.muted = self.partyList[0].supervisedByPt.muted_to_restore
                self.partyList[0].supervisedByPt.DN.otherEndParty().muted = self.partyList[0].supervisedByPt.muted_to_restore
                self.supervisedBy.partyList[0].muted = self.supervisedBy.partyList[0].muted_to_restore
        return cl

    def transfer_parameters_call_optimization(self, mainCl, mainPt, consCall=None, consPt=None):
	    """ test docstring to make sure we're on the right way """
        # common party list to test
        common_party_list = getattr(mainCl, "PartyList", []) + getattr(consCall, "PartyList", [])
        dn_to_leave = mainPt.DN
        after_transfer_party_list = [pt for pt in common_party_list if not pt.DN is dn_to_leave]
        # anti-tromboning will occur if
        # 1. All the left parties are trunks (2 parties)
        # 2. Trunks to the same T-Server
        # 3. Trunks are optimized
        if (len(after_transfer_party_list) == 2 and all([isinstance(pt.DN, SIP_Trunk) and pt.DN.optimized
                                                        for pt in after_transfer_party_list])):
            if after_transfer_party_list[0].DN.otherTrunk.tserver is after_transfer_party_list[1].DN.otherTrunk.tserver:
                return False, True
            return True, False
        return False, False

    def iscc_parameters_equalize_connid(self):
        if self.tserver.use_data_from == "current":
            return True
        return False

    def iscc_optimization_equalize_connid(self, mainCl, mainPt, consCall=None, consPt=None):
        PrintLog("iscc_optimization_equalize_connid")
        if not self.iscc_parameters_equalize_connid() or not (getattr(mainPt.DN, "optimized", False)):
            return
        if isinstance(mainPt.DN, SIP_Trunk):
            # TODO: Fix LastTransferConnID check after find out what severity does it have
            mainPt.DN.eventPartyChanged(mainPt, addPrm={"equalize_connid_iscc": True})


    def call_path_trunk_optimization(self, mainCl, mainPt, consCall=None, consPt=None):
        addPrm = {"ThirdPartyDN": mainPt.DN.number,
                  "ThirdPartyDNRole": PartyRole.TransferedBy,
                  "OtherDN": None,
                  "CallState"       : CallState.Transferred}
        # OtherDN will be changed
        trunks_to_connect = [pt.DN.otherTrunk for pt in mainCl.PartyList]
        for tr in trunks_to_connect:
            tr.resetTrunkNumber()
        for pt in copy.copy(mainCl.PartyList):
            pt.DN.eventPartyChanged(pt, addPrm=addPrm)
            self.iscc_optimization_equalize_connid(mainCl, pt)
            pt.DN.leaveCall(pt, notify=0)
        # only 2 trunks by list construction
        trunks_to_connect[0].otherTrunk = trunks_to_connect[1]
        trunks_to_connect[1].otherTrunk = trunks_to_connect[0]
        if consCall:
            consCall.Close(mainCall=mainCl)
        mainCl.Close()

    def call_path_anti_tromboning(self, mainCl, mainPt, consCall=None, consPt=None):
        addPrm = {"ThirdPartyDN": mainPt.DN.number,
                  "ThirdPartyDNRole": PartyRole.TransferedBy,
                  "CallState"       : CallState.Transferred}

        # ISCCPartyChanged will be generated
        otherEndMainPt = mainCl.PartyList[0].DN.otherEndParty()
        otherEndConsPt = mainCl.PartyList[1].DN.otherEndParty()
        otherEndMainCall = otherEndMainPt.Call
        otherEndConsCall = otherEndConsPt.Call
        otherEndMainCall.LastTransferConnID = NoConnID
        otherEndConsCall.LastTransferConnID = NoConnID
        otherEndConsCall.UserData = {}
        if self.iscc_parameters_equalize_connid():
            addPrm["ConnID"] = mainCl.ConnID
            for pt in otherEndMainCall.PartyList + otherEndConsCall.PartyList:
                PrintLog("Inside OtherDN loop")
                if pt in [otherEndMainPt, otherEndConsPt]:
                    continue
                pt.DN.eventPartyChanged(pt, addPrm=addPrm)
        for otherConsPt in copy.copy(otherEndConsCall.PartyList):
            if otherConsPt is otherEndConsPt:
                otherConsPt.DN.leaveCall(otherConsPt, notify=0)
                continue
            otherConsPt.moveToCall(otherEndMainCall, cause=Cause.CauseTransfer)
        mainCl.PartyList[-1].DN.leaveCall(mainCl.PartyList[-1], notify=0)
        # wait for PartyChanged because of anti-tromboning
        for pt in copy.copy(mainCl.PartyList):
            self.iscc_optimization_equalize_connid(mainCl, pt)
            pt.DN.leaveCall(pt, notify=0)
        otherEndMainPt.DN.leaveCall(otherEndMainPt, notify=0)
        otherEndConsCall.Close(mainCall=otherEndMainCall)
        if consCall:
            consCall.Close(mainCall=mainCl)
        mainCl.Close()

    def partyChangedEventsCompleteTransfer(self, mainCl, consCall, mainPt, consPt):

        if mainCl in consCall.ConsultList: # after alternate, swap calls
            mainCl, consCall = consCall, mainCl

        mainPtCnt = len(mainCl.PartyList)
        consPtCnt = len(consCall.PartyList)
        partyList = copy.copy(consCall.PartyList)


        for newPt in partyList:
            newPt.moveToCall(mainCl, cause=Cause.CauseTransfer) #moving all consult parties left to main call
            #newPt.DN.inheritThisQueue(mainPt, newPt) #pass
            #newPt.DN.inheritRole(mainPt, newPt) #pass
        # determine if anti-tromboning procedures should be executed
        trunk_optimization, anti_tromboning = self.transfer_parameters_call_optimization(mainCl, mainPt, consCall, consPt)
        equalize_connid_iscc = self.iscc_parameters_equalize_connid()

       # else - check for simple trunk optimization if consult was multi-site
        addPrm_trunk_addition = {"trunk_optimization": trunk_optimization,
                                 "anti_tromboning": anti_tromboning,
                                 "equalize_connid_iscc": equalize_connid_iscc}
        PrintLog("Trunk Optimization: {0}".format(trunk_optimization))
        PrintLog("Anti-tromboning: {0}".format(anti_tromboning))
        PrintLog("Equalize ConnID: {0}".format(equalize_connid_iscc))
        #last party
        lastPtFromConsCall = mainCl.PartyList[-1]
        # UUIKey
        uui_key = self.tserver.UUIKey
        if isinstance(lastPtFromConsCall.DN, Trunk0) and uui_key in consCall.UserData:
            if not uui_key in mainCl.UserData:
                anyPtFromMainCall = mainCl.PartyList[0]
                anyPtFromMainCall.DN.UpdateUserData(mainCl, userData={uui_key: consCall.UserData[uui_key]},
                                                    tserverAction = 0l)


        consCall.MoveSecondaryConsultCallsToMainCall()
        self.specialObject.processTransferCompleted(mainPt, consCall.ConsultType, mainCl, consCall, partyList)

        if mainCl.Conference:
            # Change role of new parties
            for newPt in mainCl.PartyList[mainPtCnt:]:
                newPt.Role = self.roleOfConferenceMember(newPt.Role)

        #if len(mainCl.PartyList[mainPtCnt:]) == 1: # if only one new party from cons call
        otherDN = mainCl.PartyList[-1].DN # new PARTY
        otherDNRole = mainCl.PartyList[-1].Role
        #else:
        #  otherDN = None
        #  otherDNRole = None


        addPrm = {"CallState":        CallState.Transferred,
                  "PreviousConnID":   mainCl.ConnID,
                  "ThirdPartyDN":     mainPt.DN.number,
                  "ThirdPartyDNLocation": mainPt.DN.tserver.location,
                  "OtherDN":          otherDN,
                  "OtherDNRole":      otherDNRole,
                  "ThirdPartyDNRole": PartyRole.TransferedBy}
        addPrm.update(addPrm_trunk_addition)

        PrintLog("mainCl.tserver.use_data_from = {0}".format(mainCl.tserver.use_data_from))
        if trunk_optimization:
            self.call_path_trunk_optimization(mainCl, mainPt, consCall, consPt)
        elif anti_tromboning:
            self.call_path_anti_tromboning(mainCl, mainPt, consCall, consPt)

        for oldPt in mainCl.PartyList[:mainPtCnt]: # Parties from mainCl
            if (oldPt.DN.supervisorFor and len(oldPt.DN.supervisorFor.partyList) == 0 and
                        oldPt.DN.supervisorFor.monitorScope == "agent"): #sepervised agent already left
                pass
            else:
                PrintLog("oldPt.DN.eventPartyChanged")
                oldPt.DN.eventPartyChanged(oldPt, addPrm = addPrm)


        if len(mainCl.PartyList[:mainPtCnt]) == 1: # if main call was with 2 parties (not conf)
            otherDN = mainCl.PartyList[0].DN # old PARTY
            otherDNRole = mainCl.PartyList[0].Role
        else:
            otherDN = None
            otherDNRole = None
        addPrm = {"CallState":        CallState.Transferred,
                  "PreviousConnID":   consCall.ConnID,
                  "ThirdPartyDN":     consPt.DN.number,
                  "ThirdPartyDNLocation": consPt.DN.tserver.location,
                  "OtherDN":          otherDN,
                  "OtherDNRole":      otherDNRole,
                  "ThirdPartyDNRole": PartyRole.TransferedBy,
                  "equalize_connid_iscc": equalize_connid_iscc}
        addPrm.update(addPrm_trunk_addition)
        PrintLog(str(mainCl.PartyList))
        for newPt in mainCl.PartyList[mainPtCnt:]:   # Parties from consCall
            PrintLog(str(newPt))
            PrintLog(isinstance(newPt.DN, Trunk0))
            PrintLog(isinstance(newPt.DN, SIP_Trunk))
            if consCall.ConsultType == ConsultCallType.MuteTransfer and isinstance(newPt.DN, Trunk0):
                # sometimes mutetransfer is finished on trunk, in this case no PCH
                addPrm["optional"] = "1"
            if (newPt.DN.supervisorFor and len(newPt.DN.supervisorFor.partyList) == 0 and
                        newPt.DN.monitorScope == "agent"): #sepervised agent already left. mode != connect?
                newPt.DN.leaveCall(newPt, notify = 0)
            else:
                if isinstance(newPt.DN, SIP_Trunk):
                    self.iscc_optimization_equalize_connid(mainCl, newPt)
                else:
                    newPt.DN.eventPartyChanged(newPt, addPrm=addPrm)
        consCall = consCall.Close(cause = Cause.CauseTransfer, mainCall = mainCl)
        return mainCl

    def endMonitoringSession(self):
        #almost copy of model_dn method with one fix: second if instead of elif
        if self.supervisorFor:
            for pt in self.partyList:
                if pt.supervisorForPt:
                    pt.supervisorForPt = None
                    pt.muted = 0
                    if isinstance(pt.DN, Trunk0):
                        pt.DN.otherEndParty().muted = 0
                        pt.DN.otherEndParty().supervisorForPt = None
            self.supervisorFor = None

        if self.supervisedBy:
            for pt in self.partyList:
                if pt.supervisedByPt:
                    pt.supervisedByPt = None
                    if isinstance(pt.DN, Trunk0):
                        pt.DN.otherEndParty().supervisedByPt = None
            self.supervisedBy = None
        self.monitorType = None
        self.monitorScope = None
        self.monitorMode = None
        self.monitorRequestedBy = None
        self.supervisorForLocation = None
        self.supervisionStarted = 0
        self.observingRoutingPoint = None

    def change_mute_state(self, party_to_change_mute, mute_state):
        party_to_change_mute.muted = mute_state
        if self.supervisorForLocation:
            call = party_to_change_mute.Call
            trunkparty = call.findTrunkParty()
            if trunkparty.supervisorForPt == party_to_change_mute.supervisorForPt:
                trunkparty.muted = mute_state
                otherTrunkPt = call.findTrunkPartyOnOtherSite()
                otherTrunkPt.muted = mute_state
        return True

    def check_mute_off_mute_on_private_infos(self, party, call):
        multisite = 0
        if self.supervisorForLocation:
            multisite = 1
        otherPt = call.findSupervisedParty(multisite)
        otherDN = self.supervisorFor
        addPrmPrivateInfoSupervisor = {"Extensions": {"MonitorMode": self.monitorMode},
                                       "ThisDNRole": PartyRole.Observer, "ThisDN": self.number,
                                       "OtherDNRole": PartyRole.Destination, "OtherDN": otherDN}
        self.eventPrivateInfo(party, privateMsgID=4024, addPrm=addPrmPrivateInfoSupervisor)
        if otherPt:
            addPrmPrivateInfoSupervised = {"Extensions": {"MonitorMode": self.monitorMode},
                                       "ThisDNRole": PartyRole.Destination, "ThisDN": otherPt.DN,
                                       "OtherDNRole": PartyRole.Observer, "OtherDN": self}

            otherPt.DN.eventPrivateInfo(otherPt, privateMsgID=4024, addPrm=addPrmPrivateInfoSupervised)

    def SetMuteOff(self, call=None, reasons=None, extensions=None):
        party, call = self.getPartyFromCall(None, call)
        #if self.supervisorFor and self.monitorScope == "agent" and call.MainCall:
        #    for pt in call.MainCall.PartyList:
        #        if pt.DN == party.DN:
        #            party.muted = pt.muted
        if self.tserver.SwitchPolicyFeature():
            if not party.muted or party.muted == MuteState.Coach:
                expectedAvailability = True
            else:
                expectedAvailability = False
            request = self.tserver.SetMuteOff(self.number, call.ConnID, reasons, extensions, send = 0)
            self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn = self, party = party, parties = self.partyList, expectedAvailability = expectedAvailability)
        refID = self.tserver.SetMuteOff(self.number, call.ConnID, reasons, extensions)
        self.printLog("SetMuteOff (%s, connID = %s, refID = %s)"
                      %(self.number, ConnIDToStr(call.ConnID), refID))
        addPrm = {"ReferenceID": refID}
        new_mute_state = MuteState.Connect
        if party.muted != MuteState.Connect:
            if extensions:
                if "MonitorMode" in extensions and extensions["MonitorMode"] == "coach":
                    new_mute_state = MuteState.Coach
                else:
                    new_mute_state = party.muted
        if party.is_able_to_change_mute_state(new_mute_state):
            self.eventMuteOff(party, addPrm=addPrm)
            party.change_mute_state(new_mute_state)
            if self.supervisorFor:
                if self.monitorScope == "agent" and self.supervisorFor.tserver.monitor_consult_calls != "none":
                    if self.supervisorForLocation:
                        party_supervised, call_current_otherside = self.supervisorFor.getPartyFromCall(None, None)
                        if not call_current_otherside.ConsultType:
                            party.muted_to_restore = party.muted
                            self.monitorMode_to_restore = self.monitorMode
                            call.findTrunkPartyOnOtherSite().muted_to_restore = party.muted
                    else:
                        if not call.ConsultType:
                            party.muted_to_restore = party.muted
                            self.monitorMode_to_restore = self.monitorMode
                if new_mute_state == MuteState.Coach:
                    self.monitorMode = "coach"
                else:
                    self.monitorMode = "connect"
                if self.tserver.sip_enable_call_info in ("1", "true"):
                    self.check_mute_off_mute_on_private_infos(party, call)
        else:
            self.eventError(None, addPrm=addPrm)

    def SetMuteOn(self, call=None, reasons=None, extensions=None):
        party, call = self.getPartyFromCall(None, call)
        #if self.supervisorFor and self.monitorScope == "agent" and call.MainCall:
        #    for pt in call.MainCall.partyList:
        #        if pt.DN == party.DN:
        #            party.muted = pt.muted
        if self.tserver.SwitchPolicyFeature():
            if not party.muted or party.muted == MuteState.Coach:
                expectedAvailability = True
            else:
                expectedAvailability = False
            request = self.tserver.SetMuteOn(self.number, call.ConnID, reasons, extensions, send=0)
            self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, party=party, parties=self.partyList, expectedAvailability=expectedAvailability)
        refID = self.tserver.SetMuteOn(self.number, call.ConnID, reasons, extensions)
        self.printLog("SetMuteOn (%s, connID = %s, refID = %s)"
                      % (self.number, ConnIDToStr(call.ConnID), refID))
        addPrm = {"ReferenceID": refID}
        new_mute_state = MuteState.Muted
        if party.muted != MuteState.Muted:
            if extensions:
                if "MonitorMode" in extensions and extensions["MonitorMode"] == "coach":
                    new_mute_state = MuteState.Coach
                else:
                    new_mute_state = party.muted

        if party.is_able_to_change_mute_state(new_mute_state):
            self.eventMuteOn(party, addPrm=addPrm)
            party.change_mute_state(new_mute_state)
            if self.supervisorFor:
                if self.monitorScope == "agent" and self.supervisorFor.tserver.monitor_consult_calls != "none":
                    if self.supervisorForLocation:
                        party_supervised, call_current_otherside = self.supervisorFor.getPartyFromCall(None, None)
                        if not call_current_otherside.ConsultType:
                            party.muted_to_restore = party.muted
                            self.monitorMode_to_restore = self.monitorMode
                            call.findTrunkPartyOnOtherSite().muted_to_restore = party.muted
                    else:
                        if not call.ConsultType:
                            party.muted_to_restore = party.muted
                            self.monitorMode_to_restore = self.monitorMode
                if new_mute_state == MuteState.Coach:
                    self.monitorMode = "coach"
                else:
                    self.monitorMode = "mute"
                if self.tserver.sip_enable_call_info in ("1", "true"):
                    self.check_mute_off_mute_on_private_infos(party, call)
        else:
            self.eventError(None, addPrm=addPrm)




  #-----------------------------------------

    def eventPrivateInfo(self, party=None, printEventHdr=1, addPrm=None, privateMsgID=0, addPrmStrict={}, extensionsStrict=None):
        keyAttributes = {}
        if privateMsgID:
            keyAttributes.update({"PrivateEvent": privateMsgID})
        if "ThisDNRole" in addPrm:
            return self.handleEvent("PrivateInfo", party, MonitorDNEvent, printEventHdr, addPrm, keyAttributesAndValues=keyAttributes)
        else:
            return self.handleEvent("PrivateInfo", party, DNEvent, printEventHdr, addPrm, keyAttributesAndValues=keyAttributes)

    def thirdPartyDNForSST(self, dest):
        #different from parent so it does not return DialingNumber
        if self.callToExtDN(dest):
            return str(dest.tserver.numberForInboundCall) + dest.NumberForExtCall()
        else:
            # internal call
            return dest.number

    def testCallState(self, party, event, addPrm):
        #AttributeCallState is set to Bridged for all events except EventPartyDeleted and EventReleased;
        if self.supervisorFor and event.CallState == CallState.Bridged and event.Event not in (
                "Released", "PartyDeleted"): return
        if ((event.Event == EventName.DestinationBusy and event.CallState == CallState.Busy)
            or (
                            event.Event == EventName.Abandoned and event.CallState == CallState.Busy)): return  #ok fix by Vlad Gorelov 02/04/13
        return DN.testCallState(self, party, event, addPrm)



    def GetChatSessionID(self, call=None, defaultPartyNum=-1):
        pt = None
        sessionID = 0
        if not call:
            pt = self.partyList[defaultPartyNum]  # self.partyList[-1].Call is used
        else:
            pt = GetPartyByDN(self, call)
        if pt and pt.chatSession:
            sessionID = pt.chatSession.sessionID
        return sessionID


    def ListenDisconnect(self, dest, call=None, reasons=None, extensions=None):
        pt, call = self.getPartyFromCall((PartyState.Established,), call)
        destPt, otherCall = dest.getPartyFromCall((PartyState.Established,), None)
        if otherCall <> call:  #destPt is external
            otherPt = destPt.DN.otherPartyFromCall(destPt, otherCall)
            localDestPt = otherPt.DN.otherEndParty()
        else:
            localDestPt = destPt

        time.sleep(self.tserver.requestTimout)
        if self.tserver.SwitchPolicyFeature():
            request = self.tserver.ListenDisconnect(self.number, self.destFullNum(dest), call.ConnID, reasons,
                                                    extensions, send=0)
            self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, targetDN=dest, party=pt,
                                                               parties=self.partyList, expectedAvailability=True)

        self.printLog(
            "\nListenDisconnect(%s, dest = %s, connID = %s)" % (self.number, dest.number, ConnIDToStr(call.ConnID)))

        self.tserver.ListenDisconnect(self.number, dest.number, call.ConnID, reasons, extensions)

        for p in call.PartyList:
            if localDestPt in p.talk:
                p.talk.remove(localDestPt)
            if localDestPt in p.listen:
                p.listen.remove(localDestPt)

        localDestPt.listen = []
        localDestPt.talk = []

        callState = CallState.Held
        thirdPartyDN = dest.number

        addPrm = {"CallState": callState, "OtherDN": destPt.DN, "OtherDNRole": PartyRole.ConferenceMember,
                  "ThirdPartyDN": thirdPartyDN, "ThirdPartyDNRole": PartyRole.ConferenceMember}

        self.eventListenDisconnected(pt, addPrm=addPrm)


    def ListenReconnect(self, dest, call=None, reasons=None, extensions=None):
        pt, call = self.getPartyFromCall((PartyState.Established,), call)
        destPt, otherCall = dest.getPartyFromCall((PartyState.Established,), None)
        if otherCall <> call:  #destPt is external
            otherPt = destPt.DN.otherPartyFromCall(destPt, otherCall)
            localDestPt = otherPt.DN.otherEndParty()
        else:
            localDestPt = destPt
        time.sleep(self.tserver.requestTimout)
        if self.tserver.SwitchPolicyFeature():
            request = self.tserver.ListenReconnect(self.number, self.destFullNum(dest), call.ConnID, reasons,
                                                   extensions, send=0)
            self.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self, targetDN=dest, party=pt,
                                                               parties=self.partyList, expectedAvailability=True)
        self.printLog(
            "\nListenReconnect(%s, dest = %s, connID = %s)" % (self.number, dest.number, ConnIDToStr(call.ConnID)))
        self.tserver.ListenReconnect(self.number, dest.number, call.ConnID, reasons, extensions)
        for p in call.PartyList:
            if localDestPt not in p.talk and p <> localDestPt:
                p.talk.append(localDestPt)
                localDestPt.talk.append(p)
            if localDestPt not in p.listen and p <> localDestPt:
                p.listen.append(localDestPt)
                localDestPt.listen.append(p)

        callState = CallState.Ok
        thirdPartyDN = dest.number

        addPrm = {"CallState": callState, "OtherDN": destPt.DN, "OtherDNRole": PartyRole.ConferenceMember,
                  "ThirdPartyDN": thirdPartyDN, "ThirdPartyDNRole": PartyRole.ConferenceMember}

        self.eventListenReconnected(pt, addPrm=addPrm)


    #def CallSetForward(self, dest, forwardMode = ForwardMode.Unconditional):
    #  self.printLog("\nCallSetForward(%s, dest = %s, %s)" % (self.number, dest.number, forwardMode))
    #  #No request is sent to Tserver
    #  self.forward = forwardMode
    #  self.forwardDest = dest

    #def CallCancelForward(self, forwardMode = ForwardMode.Unconditional):
    #  self.printLog("\nCallCancelForward(%s, %s)" % (self.number, forwardMode))
    #  #No request is sent to Tserver
    #  self.forward = ForwardMode.None
    #  self.forwardDest = None

    def StartChat(self, dest, location=None, userData=None, makeCallType=MakeCallType.Regular, extensions=None,
                  byDefault=0):
        return self.MakeCall(dest, location, userData, makeCallType, extensions={'chat': 'true'})

    def SendChatMessage(self, text, call=None):
        pt, call = self.getPartyFromCall((PartyState.Established, ), call)
        self.tserver.PrivateService(3000, self.number, call.ConnID,
                                    extensions={'im': text, 'im-content-type': 'text/plain'})

    def GetChatMessage(self, expectedText=None, call=None, ):
        pt, call = self.getPartyFromCall((PartyState.Established, ), call)
        lastMessage = None
        while 1:
            ev = self.tserver.WaitEventOnDN(self.number, event="PrivateInfo", timeout=2)
            if ev:
                print ev.Extensions
                if ev.Extensions and ev.Extensions.has_key('im'):
                    lastMessage = ev.Extensions['im']
            else:
                break
        self.tserver.VerifyValue(lastMessage, expectedText, "Chat message %s is not received" % expectedText)

    #VOICEMAIL

    def CheckMWI(self, new=-1, saved=-1):
        if not InTrue(self.tserver.mwi_implicit_notify):
            ProgrammWarning("To check MWI functionality set option tserver mwi-implicit-notify to true")
        if new == -1: new = self.Mailbox.getNewMessagesCnt()
        if saved == -1: saved = self.Mailbox.getSavedMessagesCnt()
        if new:
            mw = 'yes'
        else:
            mw = 'no'
        pattern = "Messages-Waiting:\s*%s.+Message-Account:\s*%s.+Voice-Message:\s*%s/%s" % (
            mw, self.Mailbox.mailBoxName, new, saved)
        res = self.SipPhone.VerifySIPMsg(msg='NOTIFY', header='content', pattern=pattern, exactMatch="re.S", dlg='dev')


    def getMailBoxDN(self):
        pt, call = self.getPartyFromCall((PartyState.Established, ), None)
        otherPt = self.otherPartyFromCall(pt)
        otherEndTrunkPt = otherPt.DN.otherEndParty()
        otherPt = otherEndTrunkPt.DN.otherPartyFromCall(otherEndTrunkPt)
        if isinstance(otherPt.DN, Trunk0):
            #repeat, external call
            #otherPt = otherPt.DN.otherPartyFromCall(otherPt)
            otherEndTrunkPt = otherPt.DN.otherEndParty()
            otherPt = otherEndTrunkPt.DN.otherPartyFromCall(otherEndTrunkPt)

        mailbox = otherPt.DN
        return mailbox

    def getMailBoxParty(self):
        pt, call = self.getPartyFromCall((PartyState.Established, ), None)
        otherPt = self.otherPartyFromCall(pt)
        otherEndTrunkPt = otherPt.DN.otherEndParty()
        otherPt = otherEndTrunkPt.DN.otherPartyFromCall(otherEndTrunkPt)
        if isinstance(otherPt.DN, Trunk0):
            #repeat, external call
            #otherPt = otherPt.DN.otherPartyFromCall(otherPt)
            otherEndTrunkPt = otherPt.DN.otherEndParty()
            otherPt = otherEndTrunkPt.DN.otherPartyFromCall(otherEndTrunkPt)
        return otherPt

    def LeaveMessage(self, tone="A", duration=8, maxPromptDuration=12):
        """Leave voice message of specified tone and duration
        parameters:
          tone            - string, epiphone tone playing as voice message
          duration        - int, message duration
          promptDuration  - int, prompt duration in seconds
        """

        mailbox = self.getMailBoxDN()
        if mailbox.greeting:
            promptDuration = mailbox.greeting.duration
        else:
            promptDuration = mailbox.standardGreetingDuration

        if mailbox.greeting:
            self.ListenToGreeting(expectedTone=mailbox.greeting.tone, duration=mailbox.greeting.duration)
        else:
            self.printLog("%s Listening to standard greeting for %s sec" % (self.globalName, promptDuration))
            time.sleep(promptDuration)

        self.SipPhone.SetConfig("play=%s" % tone)
        startTimeRx = self.SipPhone.CheckRx("_", timeoutToStabilize=maxPromptDuration, minStabTimeout=2)

        delta = time.time() - startTimeRx
        self.printLog("%s Playing message for %s sec, %s sec left" % (self.globalName, duration, duration - delta))
        assert ((duration - delta) > 0)
        time.sleep(duration - delta)  # playing message
        self.SipPhone.SendDTMF("#")
        self.SipPhone.SetConfig("play=Z")
        if GetOption("VMMessageID") == None:  # first message
            messageID = 1
            SetOption("VMMessageID", messageID)
        else:
            messageID = GetOption("VMMessageID") + 1
            SetOption("VMMessageID", messageID)

        mailbox.MessageDeposited(tone, duration)

        return messageID

    def RerecordMessage(self, tone="A", duration=8, maxPromptDuration=3):
        """Leave voice message of specified tone and duration
        parameters:
          tone            - string, epiphone tone playing as voice message
          duration        - int, message duration
        """
        mailbox = self.getMailBoxDN()
        mailbox.MessageDeleted()
        self.SipPhone.SetConfig("play=%s" % tone)
        startTimeRx = self.SipPhone.CheckRx("_", timeoutToStabilize=maxPromptDuration, minStabTimeout=2)

        delta = time.time() - startTimeRx
        self.printLog("%s Playing message for %s sec, %s sec left" % (self.globalName, duration, duration - delta))
        assert ((duration - delta) > 0)
        time.sleep(duration - delta)  # playing message
        self.SipPhone.SendDTMF("#")
        self.SipPhone.SetConfig("play=Z")
        if GetOption("VMMessageID") == None:  # first message
            messageID = 1
            SetOption("VMMessageID", messageID)
        else:
            messageID = GetOption("VMMessageID") + 1
            SetOption("VMMessageID", messageID)

        mailbox.MessageDeposited(tone, duration)

        return messageID

    def ConfirmMessage(self, dtmf="1", interruptMenuAfter=2, timeoutAfter=1):
        """Send DTMF to confirm message
        parameters:
          promptDuration  - int, prompt duration in seconds 'To confirm press...'
          dtmf - string
        """
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To confirm...'
        self.SipPhone.SendDTMF(dtmf)
        mailbox = self.getMailBoxDN()


    def ListenToRecordedMessage(self, dtmf="2", interruptMenuAfter=2):
        """Send DTMF to replay message, verify that message is the same as was recorded
        parameters:
          interruptMenuAfter  - int, prompt duration in seconds 'To confirm press...'
          dtmf - string
        """
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To confirm...'
        self.SipPhone.SendDTMF(dtmf)
        vmDN = self.getMailBoxDN()
        msg = vmDN.messages[-1]
        self.CheckMessage(expectedTone=msg.tone, interrupt=0, duration=msg.duration)


    def SetPriority(self, priority=1, interruptMenuAfter=3, timeoutAfter=2):
        """Send DTMF with message priority
        parameters:
          priority - string
          promptDuration1  - int, prompt duration in seconds 'To sent with normal priority...'
          promptDuration2  - int, prompt duration in seconds 'Message Sent'
        """
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To sent with normal priority...'
        self.SipPhone.SendDTMF(str(priority))
        self.ListenToMenu(timeoutAfter)  # listen to 'Message Sent'
        mailbox = self.getMailBoxDN()
        mailbox.SetPriority(int(priority))
        time.sleep(1)
        for owner in mailbox.owners:
            owner.CheckMWI()

    def Authenticate(self, interruptMenuAfter=2, timeoutAfter=4):
        """Enter password
        parameters:
          interruptMenuAfter - int, seconds
          timeoutAfter - int, seconds
        """
        mailbox = self.getMailBoxDN()

        if self.Mailbox == mailbox:  # internal call
            if InFalse(mailbox.tserver.auth_checkpassword_internal):
                raw_input("option - false")
                return  #- no authentication

        self.ListenToMenu(interruptMenuAfter)
        self.printLog("\n%s Enter password %s" % (self.globalName, mailbox.password))
        self.SipPhone.SendDTMF(str(mailbox.password))

        time.sleep(timeoutAfter)


    def ListenToMainMenu(self, interruptAfter=1, maxPromptTimeout=30):
        """Listen main menu
        parameters:
          interruptAfter      - int, seconds. if -1, then no interrupt, wait until Rx = '_'
          maxPromptTimeout    - int, seconds, max time until Rx = '_'
          (not a very reliable method - Rx = '_' can be on other menus)
        """
        self.printLog("\n%s Listen main menu" % (self.globalName))
        if interruptAfter == -1:
            startTimeRx = self.SipPhone.CheckRx("_", timeoutToStabilize=maxPromptTimeout, minStabTimeout=2)
        else:
            time.sleep(interruptAfter)

    def SelectIHaveMailbox(self, dtmf="1"):
        self.printLog("\n%s Select I have a mailbox" % (self.globalName))
        self.SipPhone.SendDTMF(dtmf)

    def SelectMailbox(self, mailbox, interruptMenuAfter=3, timeoutAfter=2):
        self.ListenToMenu(interruptMenuAfter)  # listen 'Enter mailbox number
        self.printLog("\n%s Enter mailbox number%s" % (self.globalName, mailbox.mailBoxName))
        self.SipPhone.SendDTMF(mailbox.mailBoxName)
        time.sleep(timeoutAfter)
        #find party and change DN
        pt = self.getMailBoxParty()
        pt.DN = mailbox


    def SelectGreetingMenu(self, dtmf="4"):
        """Select greeting menu
        parameters:
          dtmf                  - str
        """
        self.printLog("\n%s Access greeting menu" % (self.globalName))
        self.SipPhone.SendDTMF(dtmf)

    def ListenToMenu(self, interruptMenuAfter=3):
        """Listen to menu. No actual 'interruption' but function exits after timeout
        parameters:
          interruptMenuAfter      - int, seconds
        """
        time.sleep(interruptMenuAfter)

    def ChangePersonalGreeting(self, tone, dtmf="2", duration=6, interruptMenuAfter=3, maxPromptTimeout=12,
                               timeoutAfter=2):
        """Change personal greeting
        parameters:
          mailbox                    - object VM_DN
          tone                       - str (letter)
          dtmf                       - str
          interruptMenuAfter         - int, seconds
          maxPromptTimeout           - int, seconds, timeout to wait for 'beep' and following '_' Rx
        """
        mailbox = self.getMailBoxDN()
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To change your personal ...'
        self.printLog("\n%s Change Personal Greeting" % (self.globalName))
        self.SipPhone.SendDTMF(dtmf)
        self.SipPhone.SetConfig("play=%s" % tone)
        startTimeRx = self.SipPhone.CheckRx("_", timeoutToStabilize=maxPromptTimeout, minStabTimeout=1)
        delta = time.time() - startTimeRx
        self.printLog("%s Playing Greeting for %s sec" % (self.globalName, duration - delta))
        time.sleep(duration - delta)  # playing message
        self.SipPhone.SendDTMF("#")
        self.SipPhone.SetConfig("play=Z")
        mailbox.greetingMenu = "personal"
        mailbox.ChangeGreeting(tone, duration)
        time.sleep(timeoutAfter)


    def ChangeExtendedAbsenceGreeting(self, tone, dtmf="1", duration=6, interruptMenuAfter=3, maxPromptTimeout=12,
                                      timeoutAfter=2):
        """Change Change Extended Absence Greeting
        parameters:
          mailbox                    - object VM_DN
          tone                       - str (letter)
          dtmf                       - str
          interruptMenuAfter         - int, seconds
          maxPromptTimeout           - int, seconds, timeout to wait for 'beep' and following '_' Rx
        """
        mailbox = self.getMailBoxDN()
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To change your extended...'
        self.printLog("\n%s Change Extended Absence Greeting" % (self.globalName))
        self.SipPhone.SendDTMF(dtmf)
        self.SipPhone.SetConfig("play=%s" % tone)
        startTimeRx = self.SipPhone.CheckRx("_", timeoutToStabilize=maxPromptTimeout, minStabTimeout=1)

        delta = time.time() - startTimeRx
        self.printLog("%s Playing Greeting for %s sec" % (self.globalName, duration))

        time.sleep(duration - delta)  # playing message
        self.SipPhone.SendDTMF("#")
        self.SipPhone.SetConfig("play=Z")
        mailbox.greetingMenu = "extended"
        mailbox.ChangeGreeting(tone, duration)
        time.sleep(timeoutAfter)


    def ListenToGreeting(self, expectedTone, duration, timeoutToStabilize=8):
        """Listen to greeting. Verify that tone matches
        parameters:
          expectedTone   - str
          duration       - int, seconds
          timeoutToStabilize - int, seconds
        """
        self.printLog("\n%s Listen to greeting for %s" % (self.globalName, duration))
        self.SipPhone.CheckRx(expectedTone, timeoutToStabilize=timeoutToStabilize, minStabTimeout=2,
                              timeoutToLast=duration)


    def ListenToRecordedGreeting(self, dtmf="1", interruptMenuAfter=5):
        """Send DTMF to select replay recorded greeting. Listen to greeting and verify tone
        parameters:
          mailbox                 - object VM_DN
          dtmf                    - string
          interruptMenuAfter      - int, prompt duration in seconds 'To confirm press...'

        """
        mailbox = self.getMailBoxDN()
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To confirm...to listen to recorded...'
        self.SipPhone.SendDTMF(dtmf)
        if mailbox.greetingMenu == "extended":
            greeting = mailbox.greetings[1]
        elif mailbox.greetingMenu == "personal":
            greeting = mailbox.greetings[0]
        self.SipPhone.CheckRx(greeting.tone, timeoutToStabilize=12, minStabTimeout=1, timeoutToLast=greeting.duration)


    def ConfirmGreeting(self, dtmf="#", interruptMenuAfter=4):
        """Send DTMF to confirm greeting
        parameters:
          mailbox                 - object VM_DN
          dtmf                    - string
          interruptMenuAfter      - int, prompt duration in seconds 'To confirm press...'
        """

        self.ListenToMenu(interruptMenuAfter)  # listen to 'To confirm...'
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().ConfirmGreeting()

    def ActivatePersonalGreeting(self, dtmf="6", interruptMenuAfter=4, timeoutAfter=2):
        """Send DTMF to ActivatePersonalGreeting(
        parameters:
          mailbox                 - object VM_DN
          dtmf                    - string
          interruptMenuAfter      - int, prompt duration in seconds 'To confirm press...'
        """

        self.ListenToMenu(interruptMenuAfter)  # listen to 'To confirm...'
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().ActivatePersonalGreeting()
        self.ListenToMenu(timeoutAfter)  # listen to 'Personal greeting activated...'


    def ActivateExtendedAbsenceGreeting(self, dtmf="7", interruptMenuAfter=4, timeoutAfter=2):
        """Send DTMF to ActivateExtendedAbsenceGreeting
        parameters:
          mailbox                 - object VM_DN
          dtmf                    - string
          interruptMenuAfter      - int, prompt duration in seconds 'To confirm press...'
        """
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To confirm...'
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().ActivateExtendedAbsenceGreeting()
        self.ListenToMenu(timeoutAfter)  # listen to 'Extended absence greeting activated...'

    def ActivateStandardGreeting(self, dtmf="5", interruptMenuAfter=3, timeoutAfter=2):
        """Send DTMF with confirm
          interruptMenuAfter  - int, prompt duration in seconds 'To confirm press...'
          dtmf - string
        """
        self.ListenToMenu(interruptMenuAfter)  # listen to 'To confirm...'
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().ActivateStandardGreeting()
        self.ListenToMenu(timeoutAfter)  # listen to 'standard greeting activated...'

    def SelectPersonalOptionsMenu(self, dtmf="5"):
        """Access personal options
        parameters:
          dtmf                  - str
        """

        self.printLog("\n%sAccess personal options" % (self.globalName))
        self.SipPhone.SendDTMF(dtmf)


    def EnterPassword(self, newPassword, interruptMenuAfter=2):
        mailbox = self.getMailBoxDN()
        self.ListenToMenu(interruptMenuAfter)
        self.printLog("\n%s Enter new password %s" % (self.globalName, newPassword))
        mailbox.password = newPassword
        self.SipPhone.SendDTMF(str(mailbox.password))


    def ChangePassword(self, newPassword, dtmf="2", interruptMenuAfter=3, timeoutAfterPassword=6):
        """Change password
        parameters:
          mailbox               - object VM_DN
          newPassword           - str
          timeoutAfterPassword  - int, seconds
        """
        mailbox = self.getMailBoxDN()
        self.ListenToMenu(interruptMenuAfter)
        self.printLog("\n%sChange Password" % (self.globalName))
        self.SipPhone.SendDTMF(dtmf)
        self.ListenToMenu(interruptMenuAfter)
        self.EnterPassword(newPassword, interruptMenuAfter)
        time.sleep(timeoutAfterPassword)

    def ExitToMainMenu(self, dtmf="*", interruptMenuAfter=2):
        """Access Exit to main menu
        parameters:
          dtmf                  - str
          promptDuration         - int, seconds
        """
        self.ListenToMenu(interruptMenuAfter)
        self.printLog("\n%sExit to main menu" % (self.globalName))
        self.SipPhone.SendDTMF(dtmf)


    def SelectNewMessages(self, dtmf="1"):
        """Access New Messages menu
        parameters:
          dtmf                  - str
        """
        self.printLog("\n%s Select new messages" % self.globalName)
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().firstNewMsg()

    def SelectSavedMessages(self, dtmf="3"):
        """Access Saved Messages menu
        parameters:
          dtmf                  - str
        """
        self.printLog("\n%s Select saved messages" % self.globalName)
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().firstSavedMsg()

    def CheckMessage(self, expectedTone, interrupt=1, duration=0, promptDuration=25):
        if not interrupt:
            assert (duration)
            self.SipPhone.CheckRx(expectedTone, timeoutToStabilize=promptDuration, minStabTimeout=2,
                                  timeoutToLast=duration)
        else:
            self.SipPhone.CheckRx(expectedTone, timeoutToStabilize=promptDuration, minStabTimeout=2)

    def ListenToMessage(self, expectedTone, interrupt=1, duration=0, promptDuration=25):
        """Listen to message. Verify that tone matches
        parameters:
          expectedTone   - str
          interrupt      - int. Defines if message is interrupted after expected tone detected
          duration       - int, seconds. Must be specified if interrupt = 0
          promptDuration -  int, seconds, max prompt duration (timeout to stabilize the tone)
        """
        self.CheckMessage(expectedTone, interrupt, duration, promptDuration)
        self.getMailBoxDN().MessageRetrieved()
        for owner in self.getMailBoxDN().owners:
            owner.CheckMWI()


    def ReplayMessage(self, expectedTone, dtmf="11", interruptMenuAfter=2, interrupt=1, duration=10):
        """
        interruptMenuAfter - int, time for prompt 'To Replay message press...'
        """
        #prompt
        self.ListenToMenu(interruptMenuAfter)
        self.printLog("\n%s Replay message" % self.globalName)
        self.SipPhone.SendDTMF(dtmf)
        self.CheckMessage(expectedTone, interrupt, duration)


    def NextMessage(self, dtmf="#", interruptMenuAfter=0):
        self.ListenToMenu(interruptMenuAfter)
        self.printLog("\n%s Next message" % self.globalName)
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().nextMsg()

    def DeleteMessage(self, dtmf="7", interruptMenuAfter=0, timeoutAfter=1):
        self.ListenToMenu(interruptMenuAfter)
        self.printLog("\n%s Delete message" % self.globalName)
        self.SipPhone.SendDTMF(dtmf)
        self.getMailBoxDN().MessageDeleted()
        if self.Mailbox.newOrSaved == "new":
            self.getMailBoxDN().nextNewMsg()
        elif self.Mailbox.newOrSaved == "saved":
            self.getMailBoxDN().nextSavedMsg()
        self.ListenToMenu(timeoutAfter)


    def PauseListenMessage(self, dtmf="0"):
        self.printLog("\n%s Pause message" % self.globalName)
        self.SipPhone.SendDTMF(dtmf)


    def ResumeListenMessage(self, dtmf="0"):
        self.printLog("\n%s Resume message" % self.globalName)
        self.SipPhone.SendDTMF(dtmf)


class SIP_RouteDN(SIP_Address, RouteDN):
    def __init__(self, number, register=1, numberForCall=None, numberForExtCall=0, routePointType=0, tserver=None,
                 sipNode=None, controllerType="SessionController"):
        SIP_Address.__init__(self, number, tserver, sipNode, controllerType)
        RouteDN.__init__(self, number, register, numberForCall, numberForExtCall, tserver=tserver,
                         routePointType=routePointType)
        self.URI = "sip:%s@%s" % (self.number, self.tserver.sipAddr)
        self.ipURI = "sip:%s@%s" % (self.number, self.tserver.sipIPAddr)
        self.NetworkReachedOnCallingParty = 1
        self.routeRequestOnly = 0
        if self.cfgObj.type == CfgDNType.CFGVirtRoutingPoint:
            self.routeRequestOnly = 1
        self.divert_on_ringing = "default"  #divert-on-ringing option support parameter
        if self.cfgDN:
            divert_on_ringing = self.FindOption("TServer", "divert-on-ringing")
            if divert_on_ringing:
                self.divert_on_ringing = divert_on_ringing
        self.routeRequestRefID = 0  # added to process release when divert-on-ringing="false"


    def setDefaultOptionValues(self):
        model_address.Address.setDefaultOptionValues(self)
        self.divert_on_ringing = "default"

    def getHgMembers(self):
        return []

    def eventTreatmentEnd(self, party, printEventHdr=1, addPrm=None):
        return self.handleEvent("TreatmentEnd", party, SipTreatmentEvent, printEventHdr, addPrm)

    def CollectDTMF(self, call=None, digits="", collectionStopped=0):

        party, call = self.getPartyFromCall(None, call)
        otherPt = self.otherPartyFromCall(party, call)
        #if otherPt and not otherPt.sentDigits:
        #  ProgrammError("SendDTMF is was not executed")
        if not party.treatmentType in (TreatmentType.CollectDigits,
                                       TreatmentType.PlayAnnouncementAndDigits):
            ProgrammError(
                "CollectDigits is valid only for TreatmentType.CollectDigits, TreatmentType.PlayAnnouncementAndDigits")

        if collectionStopped:
            self.eventError(None, addPrm={"ErrorCode": 1700})

        if digits == "": return
        addPrm = {"TreatmentType": party.treatmentType, "ReferenceID": party.treatmentRefID, "CollectedDigits": digits}
        self.eventTreatmentEnd(party, addPrm=addPrm)
        self.treatmentStop(party)

    def destPartyRing(self, call, party, dest, callState=CallState.Ok):
        saved_link_conferencedBy = party.get_link_by_property_reverse("conferencedBy")
        if self.supervisedBy and not len(self.supervisedBy.partyList):
            #setting monitor session
            substObserver = self.supervisedBy
            dest.supervisedBy = self.supervisedBy
            dest.monitorType = self.monitorType
            dest.monitorScope = self.monitorScope
            dest.monitorMode = self.monitorMode
            dest.monitorRequestedBy = self.monitorRequestedBy
            substObserver.lastSuperviserFor = dest
            substObserver.supervisorFor = dest
            #end setting monitor session
        ringing_pts = dest.ring(call, thisQueue=self)
        ringing_pts = ringing_pts if isinstance(ringing_pts, tuple) else (ringing_pts, )
        parties_to_set_link = []
        for pt in ringing_pts:
            if isinstance(pt.DN, Trunk0):
                parties_to_set_link += pt.get_other_end_real_parties(dest)
                continue
            parties_to_set_link.append(pt)

        # inherit required properties
        saved_link_conferencedBy.set_link_by_property(parties_to_set_link, "conferencedBy")
        return ringing_pts  #role = dest.inheritRole(party)) #for UK

    def distrCallToDestination(self, call, dn):
        """completion of call when divert-on-ringing=false and destination answers"""
        uncompt = call.pendingQueue
        call.pendingQueue = None
        uncompt.completeMakeCall(uncompt.partyList[0], dn)

    def processRouteDestReleased(self, call, dn):
        """handling of an error event when divert-on-ringing=false and destination releases"""
        distr_parties = call.findDistributionDeviceParties()
        dn_and_trunk_parties = call.findDNandTrunkParties()
        if len(distr_parties) == 1 and len(dn_and_trunk_parties) == 1:
            if dn_and_trunk_parties[0].Role == PartyRole.Origination:
                Address.eventError(self, addPrm={"ReferenceID": self.routeRequestRefID})
            elif dn_and_trunk_parties[0].Role != PartyRole.ConferenceMember:
                distr_parties[0].removeFromCall()
                call.pendingQueue = None
                dn_and_trunk_parties[0].DN.leaveCall(dn_and_trunk_parties[0], abandPermited=1)


    def eventRinging(self, party, printEventHdr=1, addPrm=None):
        if not self.routeRequestOnly:
            cl = party.Call.IfConsultMustCompleteDoItByParty(self, "CallPartyMoved")
            party.Call = cl

            ev = self.tserver.WaitEvent(self.number)
            if ev:
                self.tserver.PutBackEvent(ev)

            if ev and ev.Event == "RouteRequest":
                party.Call = party.Call.IfConsultMustCompleteDoIt(self,
                                                                  EventName.RouteRequest)  # This is for cases when mutetransfer is completed
                self.eventRouteRequest(party, addPrm=addPrm)
                party.Call = party.Call.IfConsultMustCompleteDoIt(self)
                ev = self.eventQueued(party, addPrm=addPrm)
            else:
                # default sequence
                party.Call = party.Call.IfConsultMustCompleteDoIt(self,
                                                                  EventName.Queued)  # This is for cases when mutetransfer is completed
                self.eventQueued(party, printEventHdr, addPrm)
                if addPrm and addPrm.has_key("CallState") and (addPrm["CallState"] == CallState.Transferred or \
                                                                           addPrm[
                                                                               "CallState"] == CallState.Redirected or
                                                                       addPrm["CallState"] == CallState.Forwarded):
                    addPrm["CallState"] = CallState.Ok
                party.Call = party.Call.IfConsultMustCompleteDoIt(
                    self)  # This is for cases when mutetransfer is completed
                ev = self.eventRouteRequest(party, addPrm=addPrm)
            party.stateChanged(newTState=TPartyState.PtState_Queued | TPartyState.PtState_Routing)
            # vgratsil 02/13/2014 - basic fix for the cases when router is connected and issues AttachData
            # this will process ALL events distributed by other T-Lib clients for call being routed.
            # SIP_Tserver option used to avoid unnecessary Wait instructions and test execution time reduction.
            if self.tserver.isRouterConnected:
                self.tserver.Wait()

            if party.Call.PredictOrig:
                party.Call.CallID = None
            return ev
        else:
            party.Call = party.Call.IfConsultMustCompleteDoIt(self)
            return self.eventRouteRequest(party, printEventHdr, addPrm)


    def afterRouteCall(self, pt, dest, location, routeType, byDefault):
        """ if divert-on-ringing=false RP as a pending queue is added to call"""
        call = pt.Call
        pt.makeCallCompleted = 0
        if routeType == RouteType.Reject:
            return self.rejectedRoute(pt, None)
        elif routeType == RouteType.CallDisconnect:
            return self.rejectedRouteCallDisconnect(pt, None)
        else:
            if byDefault:
                pt.routeCallReferenceID = 0
                pt.routedByDefault = 1

            extCall = 0
            if self.callToExtDN(dest):
                dest = self.trunk(self, dest)
                call.external = 1
                extCall = 1
                if location:
                    call.ViaExtRouter = 1
            if not isinstance(dest, SIP_RouteDN) or isinstance(dest, SIP_Queue):
                if (self.divert_on_ringing in ("0", "false") or
                   (self.divert_on_ringing =="default" and self.tserver.divert_on_ringing in ("0", "false"))):
                    call.pendingQueue = self
                    self.routeRequestRefID = self.tserver.refID
            self.destPartyRing(call, pt, dest)

    def completeMakeCall(self, party, dest, addPrm=None):
        """completion of call is delayed when RP stays in call due to divert-on-ringing=false"""
        if party.makeCallCompleted:  return
        if party.State == PartyState.Dialing:
            return model_address2.Address2.completeMakeCall(self, party, dest)

        #if party.Call.ViaExtRouter and self.tserver.TransactionMonitoringFeature() and \
        #  not isinstance(self, self.tserver.ExtRoutePoint):
        #  transaction = self.tserver.TransactionMonitor.CreateTransaction(party.Call, "source")

        resources = self.resourcesCompleteMakeCall(party, dest)
        call, callingParty, ringingParty, establishedParty, destParty, otherPt, extCall = resources
        if not call.pendingQueue or not isinstance(call.pendingQueue, SIP_RouteDN):
            addPrmRouteUsed, addPrmDiverted = self.addPrmCompleteMakeCall(party, dest, otherPt, extCall, addPrm)
            self.completeMakeCallRouteEvents(party, dest, addPrmRouteUsed, addPrmDiverted)
            self.updateCallDataISCC(party)
            PrintLog(call.PartyList)
            party.removeFromCall(cause=Cause.CauseRoute)
            if extCall:
                self.completeMakeCallOutboundRoute(party, dest, destParty, callingParty)
            party.makeCallCompleted = 1
            #if not call.pendingQueue:
            #    call.pendingQueue = None
        return call

    def addPrmCompleteMakeCall(self, party, dest, otherPt, extCall, previousAddPrm=None):
        addPrmRouteUsed, addPrmRouteDiverted = RouteDN.addPrmCompleteMakeCall(self, party, dest, otherPt, extCall,
                                                                              previousAddPrm)
        if self.observerRP == 1:
            found_customer = 0
            if party and party.Call:
                for pt in party.Call.PartyList:
                    if pt.customer == 1:
                        found_customer = 1
                        if isinstance(pt.DN, Trunk0):
                            otherEndTrunkPt = pt.DN.otherEndParty()
                            if otherEndTrunkPt:
                                pt = OtherParty(pt.DN.otherEndCall(), otherEndTrunkPt)
                        addPrmRouteUsed["OtherDN"] = pt.DN
                        addPrmRouteUsed["OtherDNRole"] = pt.Role
                        addPrmRouteDiverted["OtherDN"] = pt.DN
                        addPrmRouteDiverted["OtherDNRole"] = pt.Role
                        print "customer party", pt
                        break
            addPrmRouteUsed["ThisDNRole"] = PartyRole.Observer
            addPrmRouteDiverted["ThisDNRole"] = PartyRole.Observer
        # Vlad Gorelov fix 02/14/14 When call is  predictive wrong ThirdPartyDNRoles expected
        if party and party.Call and party.Call.PredictOrig:
            addPrmRouteUsed["ThirdPartyDNRole"] = PartyRole.Destination
            addPrmRouteDiverted["ThirdPartyDNRole"] = PartyRole.Destination
        return (addPrmRouteUsed, addPrmRouteDiverted)


class SIP_Supplementary(SIP_Address, SIP_RouteDN):
    def __init__(self, number, queue, tserver=None):
        SIP_RouteDN.__init__(self, number, tserver=tserver)
        self.queue = queue


    def otherPartyCompleteMakeCall(self, party, destParty):
        if destParty == party.queuePt:
            otherPt = self.otherPartyFromCallExcept(party, [destParty])
        else:
            otherPt = self.otherPartyFromCallExcept(party, [destParty, party.queuePt])
        return otherPt

    def completeMakeCall(self, party, dest, addPrm=None):

        if party.makeCallCompleted:  return
        resources = self.resourcesCompleteMakeCall(party, dest)
        call, callingParty, ringingParty, establishedParty, destParty, otherPt, extCall = resources
        qPt = ringingParty.queuePt

        addPrmRouteUsed, addPrmDiverted = self.addPrmCompleteMakeCall(party, dest, otherPt, extCall, addPrm)
        self.completeMakeCallRouteEvents(party, dest, addPrmRouteUsed, addPrmDiverted)
        self.updateCallDataISCC(party)

        qPt.DN.eventDiverted(qPt, addPrm=addPrmDiverted)
        qPt.removeFromCall()

        party.removeFromCall(cause=Cause.CauseRoute)
        if extCall:
            self.completeMakeCallOutboundRoute(party, dest, destParty, callingParty)
        party.makeCallCompleted = 1
        return call

    def destPartyRing(self, call, party, dest, callState=CallState.Ok):
        #return dest.ring(call, thisQueue = self, role = dest.inheritRole(party)) #for UK
        if self.supervisedBy and not len(self.supervisedBy.partyList):
            #setting monitor session
            substObserver = self.supervisedBy
            dest.supervisedBy = self.supervisedBy
            dest.monitorType = self.monitorType
            dest.monitorScope = self.monitorScope
            dest.monitorMode = self.monitorMode
            dest.monitorRequestedBy = self.monitorRequestedBy
            substObserver.lastSuperviserFor = dest
            substObserver.supervisorFor = dest
            #end setting monitor session
        return dest.ring(call, thisQueue=self.queue)

    def DoNotRouteCall(self, timeout, call=None):
        party, call = self.getPartyFromCall((PartyState.Ringing,), call)
        qPt = party.queuePt
        self.printLog("%s do not route call for % sec. Waiting for call to be distrubited from queue %s" % (
            self.number, timeout, self.queue.number))
        time.sleep(timeout)
        dest = self.queue
        resources = self.resourcesCompleteMakeCall(party, dest)
        call, callingParty, ringingParty, establishedParty, destParty, otherPt, extCall = resources
        addPrmRouteUsed, addPrmDiverted = self.addPrmCompleteMakeCall(party, dest, otherPt, extCall)
        self.completeMakeCallRouteEvents(party, dest, addPrmRouteUsed, addPrmDiverted)
        self.updateCallDataISCC(party)
        party.removeFromCall(cause=Cause.CauseRoute)
        res = self.queue.distrCall(qPt.Call)
        if res: return res


class SIP_HuntGroup(object):
    members = []
    hgType = None


    def __init__(self, queue):
        self.queue = queue
        self.member_to_distribute = 0



    def setHgType(self, hgTypeToSet):
        """Sets HG type to given value.
            Parameters:
                hgTypeToSet - HG type to set. Valid values - string or None.
            Return:
                None"""
        self.hgType = hgTypeToSet


    def getHgType(self):
        """Returns HG type"""
        return self.hgType


    def setHgMembers(self, membersToAdd):
        """Sets HG members to a given list.
        Parameters:
            membersToAdd - list of DN objects to set as HuntGroup members
        Returns:
            None"""
        self.members = membersToAdd

    def getHgMembers(self):
        """Returns HG members"""
        return self.members


    def getReadyDNs(self):
        """Returns such HG members, that do not have any active parties.
        Returns:
            List of DN objects from HG members, that are ready to accept call"""
        readyMembers = []
        hgtype = self.getHgType()
        if hgtype == "fork":
            for dn in self.getHgMembers():
                if not len(dn.partyList):
                    readyMembers.append(dn)
        elif hgtype in ("linear", "circular"):
            for dn in self.getHgMembers()[self.member_to_distribute: ]:
                if not len(dn.partyList) and not dn.release_on_hg_call:
                    readyMembers.append(dn)
                    break
            if hgtype == "circular" and not len(readyMembers):
                for dn in self.getHgMembers()[ :self.member_to_distribute]:
                    if not len(dn.partyList) and not dn.release_on_hg_call:
                        readyMembers.append(dn)
                        break

        PrintLog(readyMembers)
        return readyMembers


    def isHgActive(self):
        """Returns either the HG is active (it means, has sufficient configuration.
        It means HG object has HG type set to not None
        Return:
            True, if HG type set to not-None value
            False, if HG type is None"""
        if self.getHgType():
            return True
        return False


    def isOverflowNeeded(self):
        if self.isHgActive() and len(self.getReadyDNs()):
            return False
        return True


    def distributeCall(self, call):
        """Distributes call from ACD Queue to all ready HG members.
        Only "fork" manner is supported now.
        If no members are available for a call, then executes performOverflow method from its queue
        Returns:
            Tuple with all ringing parties"""
        call.toHg = True
        call.pendingQueue = self.queue
        queuePt = self.queue.partyToDistribute()
        parties = []
        if self.getHgType() in ("fork", "linear", "circular"):
            for dn in self.getReadyDNs():
                parties.append(self.queue.destPartyRing(call, queuePt, dn, callState=CallState.Covered))
        if not len(parties):
            return self.queue.performOverflow(call)
        return tuple(parties)


class SIP_Queue(SIP_Address, Queue):
    def __init__(self, number, register=1, numberForLogin=None, numberForCall=None, numberForExtCall=None,
                 tserver=None):
        self.huntGroup = SIP_HuntGroup(self)
        self.hg_type = None
        self.hg_members = None
        Queue.__init__(self, number, register, numberForCall=numberForCall, numberForExtCall=numberForExtCall,
                       tserver=tserver)
        self.URI = "sip:%s@%s" % (self.number, self.tserver.sipAddr)
        self.ipURI = "sip:%s@%s" % (self.number, self.tserver.sipIPAddr)
        self.CDN = None

        if numberForLogin == "register-cdn":  # association must be configured in CME
            if self.cfgDN.association == "":
                ProgrammError("CDN cannot be assigned, no association is configured for ACD %s in CME" % self.number)

            rpNumber = self.cfgDN.association
            self.CDN = SIP_Supplementary(rpNumber, queue=self, tserver=tserver)

    def setDefaultOptionValues(self):
        model_address.Address.setDefaultOptionValues(self)
        self.hg_members = None
        self.hg_type = None
        self.setHgMembers([])
        self.setHgType(None)

    def ApplyCfgChanges(self):
        model_address.Address.ApplyCfgChanges(self)
        if self.hg_type:
            self.setHgType(self.hg_type)
        if self.hg_members:
            number_list = self.hg_members.split(",")
            dn_number_voc = {}
            for item in self.tserver.ObjectList:
                if isinstance(item, SIP_DN):
                    dn_number_voc.update({item.number:item})
            object_list=[]
            for number in number_list:
                if number in dn_number_voc.keys():
                    object_list.append(dn_number_voc[number])
            self.setHgMembers(object_list)

    def setHgMembers(self, members):
        """Sets HG members to a given list.
        Parameters:
            membersToAdd - list of DN objects to set as HuntGroup members
        Returns:
            None"""
        self.huntGroup.setHgMembers(members)


    def getHgType(self):
        """Returns HG type"""
        return self.huntGroup.getHgType()


    def setHgType(self, hgTypeToSet):
        """Sets HG type to given value.
           Parameters:
               hgTypeToSet - HG type to set. Valid values - string or None.
           Return:
               None"""
        self.huntGroup.setHgType(hgTypeToSet)


    def getHgMembers(self):
        """Returns HG members"""
        return self.huntGroup.getHgMembers()


    def ring(self, call, callState=CallState.Ok, thisQueue=None, role=None, addPrm=None):
        dialPt = GetPartyByState(PartyState.Dialing, call)  #trunk
        if call.Scope == CallScope.Inbound and dialPt and self.CDN:
            ringPtCdn = self.CDN.ring(call, callState, thisQueue, role, addPrm)
            ringPtCdn.makeCallCompleted = 1  # to prevent calling completeMakeCall in queue.ring
            ringPtQueue = Address2.ring(self, call, callState, thisQueue=self, role=role, addPrm=addPrm)
            linkedPartyToRestore = ringPtQueue.get_link_by_property_reverse("conferencedBy")
            ringPtCdn.makeCallCompleted = 0  # back
            ringPtQueue.cdnPt = ringPtCdn
            ringPtCdn.queuePt = ringPtQueue
            ringPtQueue.clean_link_by_property("conferencedBy")
            linkedPartyToRestore.set_link_by_property(ringPtCdn, "conferencedBy")
            return ringPtCdn
        else:
            ringing_pts = Queue.ring(self, call, callState, thisQueue, role, addPrm)
            ringing_pts = ringing_pts if isinstance(ringing_pts, tuple) else (ringing_pts, )
            return ringing_pts


    def destPartyRing(self, call, party, dest, callState=CallState.Ok):
        PrintLog("DestPartyRing")
        PrintLog(str(dest.number))
        if self.supervisedBy and not len(self.supervisedBy.partyList):
            #setting monitor session
            substObserver = self.supervisedBy
            dest.supervisedBy = self.supervisedBy
            dest.monitorType = self.monitorType
            dest.monitorScope = self.monitorScope
            dest.monitorMode = self.monitorMode
            dest.monitorRequestedBy = self.monitorRequestedBy
            substObserver.lastSuperviserFor = dest
            substObserver.supervisorFor = dest
            #end setting monitor session
        return dest.ring(call, thisQueue=self, role=dest.inheritRole(party), callState=callState)


    def otherRealPartyFromCall(self, party, cl=None):
        if not cl:
            cl = party.Call
        if not (cl.toHg and cl.pendingQueue):
            return Queue.otherRealPartyFromCall(self, party, cl)
        parties = [prt for prt in cl.PartyList if not (prt.DN in cl.pendingQueue.getHgMembers() + [cl.pendingQueue])]
        if len(parties): return parties[0]
        return None
        #if not cl:
        #    cl = party.Call
        #otherPt = None
        #for pt in cl.PartyList:
        #    PrintLog("SIP_Queue - otherRealPartyFromCall - {0} {1} {2}".format(pt, pt is not party, type(pt.DN)))
        #    if (pt is not party) and (not isinstance(pt.DN, SIP_Supplementary)):
        #        if not otherPt: otherPt = pt
        #        else:           return None  # Other party is not uniqe
        #return otherPt


    def performOverflow(self, call):
        """Method, called on Queue to perform overflow"""
        overFlowDest = self.getOverflowDest()
        if not overFlowDest:
            self.huntGroup.member_to_distribute = 0
            PrintLog("+++++++Debug: Under construction+++++")
            return
        PrintLog("Waiting overflow timeout %s sec" % self.overflowTimeout)
        time.sleep(self.overflowTimeout)
        if overFlowDest.tserver <> self.tserver:
            overFlowDest = self.trunk(self, overFlowDest)
            if InTrue(GetOption("CofFeature")):
                call.ViaExtRouter = 1
                call.external = 1
        pt = self.partyToDistribute()
        thirdPartyDNRole = PartyRole.Destination
        if pt.Role == PartyRole.ConferenceMember and len(pt.Call.PartyList) >= 3:
            thirdPartyDNRole = PartyRole.ConferenceMember
        thirdPartyDN = "Trunk"
        addPrm = {"ThirdPartyDN": thirdPartyDN, "ThirdPartyDNRole": thirdPartyDNRole}
        if not self.routeRequestOnQueued:
            ev = self.mayBeEvent(EventName.Diverted, pt, timeout=3, addPrm=addPrm)
        else:
            addPrmRU = {"ReferenceID": 0, "Reasons": None, "ThirdPartyDN": thirdPartyDN,
                        "ThirdPartyDNRole": thirdPartyDNRole}
            ev = self.mayBeEvent(EventName.RouteUsed, pt, timeout=3, addPrm=addPrmRU)
            ev = self.mayBeEvent(EventName.Diverted, pt, timeout=3, addPrm=addPrm)
        if not ev:
            pt.postponedAbandonedOrDiverted = 1
            self.postponedAbandonedOrDiverted = self.postponedAbandonedOrDiverted + 1
        pt.removeFromCall()
        ringPt = overFlowDest.ring(call)
        return ringPt


    def distrCall(self, call, callState=CallState.Ok):
        """Find agent and distribute call for him.
        Param:
          call - Call; call which we have to distribute to agent

        Return - Party / None; party of agent dn, None in case when there is no ready agent.
        """

        # HG way
        if self.huntGroup.isHgActive():
            return self.huntGroup.distributeCall(call)

        readyNumList = []
        for ag in self.agentList:
            res = ag.readyToGetCall()
            if res:
                readyNumList.append(ag.dnForCalls().number)
                if res == 2: break
        if not len(readyNumList):
            return self.performOverflow(call)

        # We look ahead on event to understand what agent really get the call
        eventToPutBackList = []
        agentToDistribute = None
        i = 0
        if self.tserver.linkConnected:
            while i < 12:
                ev = self.tserver.WaitEvent(timeout=2)
                if ev:
                    if (self.tserver.callMonitoringDN and ev.ThisDN == self.tserver.callMonitoringDN.number):
                        pass
                    else:
                        if ev.Event == EventName.DNOutOfService:
                            self.tserver.actionOnUnexpectedEvent(ev.ThisDN, ev)
                        else:
                            eventToPutBackList.insert(0, ev)
                        if self.getHgType(): break
                        if (ev.Event == EventName.Ringing) and (ev.ThisDN in readyNumList):
                            for ag in self.agentList:
                                if ag.dnForCalls().number == ev.ThisDN:
                                    agentToDistribute = ag
                                    break
                            break
                        i = i + 1
                else:
                    i = i + 1
            for ev in eventToPutBackList:
                self.tserver.PutBackEvent(ev)

            if not agentToDistribute:
                self.tserver.serCallDisrProblemCnt = self.tserver.serCallDisrProblemCnt + 1

                evntList = ""
                i = len(eventToPutBackList) - 1
                while i >= 0:
                    evntList = evntList + ("    %s\n" % str(eventToPutBackList[i]))
                    i = i - 1

                self.tserver.SeriousError("Can't distribute call from queue %s" % self.number, evntList, forceReset=1)

        return self.distrCallToAgent(agentToDistribute, callState=callState)


    def distrCallToDestination(self, call, dn):
        """Method to call on queue if it is pending (i.e. divert-on-ringing=false or HG configuration).
        Generates Diverted and handles all other ringing parties release from call"""
        pt = self.partyToDistribute()
        thirdPartyDNRole = PartyRole.Destination
        if pt.Role == PartyRole.ConferenceMember and len(pt.Call.PartyList) >= 3:
            thirdPartyDNRole = PartyRole.ConferenceMember
        if dn:
            thirdPartyDN = dn.number
        if not self.routeRequestOnQueued:
            self.eventDiverted(pt, addPrm={"ThirdPartyDN": thirdPartyDN, "ThirdPartyDNRole": thirdPartyDNRole})
        else:
            self.eventRouteUsed(pt, addPrm={"ReferenceID": 0, "Reasons": None, "ThirdPartyDN": thirdPartyDN,
                                            "ThirdPartyDNRole": thirdPartyDNRole})
            self.eventDiverted(pt, addPrm={"ThirdPartyDN": thirdPartyDN, "ThirdPartyDNRole": thirdPartyDNRole})
        pt.removeFromCall()
        for dnpt in copy.copy(call.PartyList):
            if dnpt.State == PartyState.Ringing and not dnpt.DN.supervisedBy and dnpt.DN != dn:
                dnpt.DN.leaveCall(dnpt, addPrm={"CallState": CallState.Redirected})
        call.pendingQueue = None
        if self.hg_type == "linear":
            self.huntGroup.member_to_distribute = 0
        if self.hg_type == "circular":
            self.huntGroup.member_to_distribute = (self.huntGroup.member_to_distribute + 1) % len(self.getHgMembers())
        call.release_counter_on_hg = 0
        for dn in self.huntGroup.members:
            dn.release_on_hg_call = 0





    def processRouteDestReleased(self, call, dn):
        """Method to call, when this queue is pending and distribution destination leaves the call."""

        self.huntGroup.member_to_distribute += 1
        call.release_counter_on_hg += 1
        thirdPartyDNRole = PartyRole.Destination
        if dn:
            thirdPartyDN = dn.number
            dn.release_on_hg_call = 1
        if self.getHgType() == "fork":
            dnpts = GetPartiesByState(PartyState.Ringing, call)
            ## vgratsil - 01-29-2014
            #TODO: make handling of call overflow
            if len(dnpts) <> 1:
                return
        elif self.getHgType() in ("linear", "circular"):
            if call.release_counter_on_hg < len(self.getHgMembers()):
                if self.getHgType() == "circular":
                    self.huntGroup.member_to_distribute %= len(self.getHgMembers())
                return self.huntGroup.distributeCall(call)
        dialParty = GetPartyByState(PartyState.Dialing, call)
        if dialParty and not dialParty.DN == self:
            dialParty.DN.leaveCall(dialParty, addPrm={"CallState": CallState.Redirected})
        pt = self.partyToDistribute()
        self.eventDiverted(pt, addPrm={"CallState": CallState.Dropped, "ThirdPartyDN": thirdPartyDN,
                                   "ThirdPartyDNRole": thirdPartyDNRole})
        if pt:
            pt.removeFromCall()


    def leaveCall(self, party, notify=1, abandPermited=1, addPrm=None, cause=Cause.CauseEMPTY):

        call = party.Call
        notifyToUse = notify
        abandPermitedToUse = abandPermited
        if call.pendingQueue:
            return
        cl = Queue.leaveCall(self, party, notifyToUse, abandPermitedToUse, addPrm, cause)
        return cl








class SIP_Agent(Agent):
    def Login(self, queue=None, dn=None, passwd="", agentType=AgentType.Agent,
              workMode=AgentWorkMode.ManualIn, reasons=None, extensions=None, byDefault=0):

        if queue:
            return Agent.Login(self, queue, dn, passwd, agentType,
                               workMode=workMode, reasons=reasons, extensions=extensions)
        else:
            if not passwd:
                passwd = self.defaultPassword
            if not dn:
                dn = self.defaultDN
            self.workMode = workMode
            self.agentType = agentType

            bdf = ""
            if byDefault:
                bdf = "by default"
            dn.printLog("\nLogin(%s, dn = %s) %s" % (self.id, dn.number, bdf))
            refID = 0
            if not byDefault:
                time.sleep(dn.tserver.requestTimout)
                refID = self.loginRequest(queue, dn, passwd, agentType,
                                          workMode, reasons, extensions)
            if self not in dn.tserver.AgentList:
                dn.tserver.AgentList.append(self)

            if not self.errorLoginEvents(queue, dn, refID):
                dn.defaultAgentLogin = self
                #if self.defaultExtension:
                #  self.defaultExtension.defaultAgentLogin = self
                self.dn = dn
                dn.agentLogin = self
                self.dn.autoAnswer = self.autoAnswer
                self.defaultDN = dn
                self.loginEvents(workMode, refID)
                self.getCfgAgent()

    def loginRequest(self, queue, dn, passwd="", agentType=AgentType.Agent,
                     workMode=AgentWorkMode.Unknown, reasons=None, extensions=None):
        if queue:
            return Agent.loginRequest(self, queue, dn, passwd, agentType,
                                      workMode, reasons, extensions)
        else:
            if dn.tserver:
                if dn.tserver.SwitchPolicyFeature():
                    available = True
                    if self.errorLoginEventsExpected(queue, dn): available = False
                    request = dn.tserver.AgentLogin('', dn.number, self.id, self.agentType, passwd,
                                                    self.workMode, reasons, extensions, send=0)
                    dn.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=dn, expectedAvailability=available)
                refID = dn.tserver.AgentLogin('', dn.number, self.id, self.agentType, passwd,
                                              self.workMode, reasons, extensions)
                return refID
            return 0

    def Logout(self, queue=None, reasons=None, extensions=None, byDefault=0):
        # fix by Vlad Gorelov 08/01/13 Added processing flag dropNailedupOnLogout
        if self.dn:
            if (self.dn.tserver.drop_nailedup_on_logout in ("1", "true")):
                self.dn.parked = 0
        #end of fix
        if queue:
            Agent.Logout(self, queue, reasons, extensions, byDefault)
        else:
            if self.state == AgentState.Logout:
                ProgrammWarning("Agent is in Logout state")
                return
            if self.dn:
                bdf = ""
                if byDefault:
                    bdf = "by default"
                self.dn.printLog("\nLogout %s %s" % (self.id, bdf))
                refID = 0
                if not byDefault:
                    time.sleep(self.dn.tserver.requestTimout)
                    if self.dn.tserver.SwitchPolicyFeature():
                        available = True
                        if not found: available = False
                        request = self.dn.tserver.AgentLogout('', self.dn.number, reasons, extensions, send=0)
                        self.dn.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self.dn,
                                                                              parties=self.dn.partyList,
                                                                              expectedAvailability=available)
                    refID = self.dn.tserver.AgentLogout('', self.dn.number, reasons, extensions)
                self.logoutEvents(refID)

            else:
                if self.defaultDN and self.defaultDN.tserver.SwitchPolicyFeature():
                    request = self.dn.tserver.AgentLogout('', self.defaultDN.number, reasons, extensions, send=0)
                    self.defaultDN.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self.defaultDN,
                                                                                 expectedAvailability=False)
                    return
                else:
                    ProgrammError("Agent is not logged into DN")

    #logoutEventsOnWrongQueue method is different from parent because for SIP it does not matter what queue it is being logged out
    def logoutFromWrongQueue(self, queue, refID, byDeafult):
        # agent was not logged into this queue
        if self.queueList:
            #logout from default (the obly one) queue
            queue = self.queueList[0]
            queue.removeAgent(self)
            self.removeQueue(queue)
            self.dn.agentLogin = None
            self.logoutEvents(refID)
        else:
            if not byDefault:
                self.dn.eventError(None, self.dn)
            else:
                ProgrammWarning("Agent is not logged into Queue %s" % queue.number)

    def SetReady(self, queue=None, workMode=AgentWorkMode.AutoIn, reasons=None, extensions=None, ignoreState=0):
        if queue:
            return Agent.SetReady(self, queue, workMode, reasons, extensions)
        else:
            if self.dn:
                self.dn.printLog("\nSetReady (%s)" % (self.id))
                if not ignoreState:
                    if self.state == AgentState.Ready:
                        ProgrammWarning("Agent is in Ready state")
                        return
                time.sleep(self.dn.tserver.requestTimout)
                if self.dn.tserver.SwitchPolicyFeature():
                    request = self.dn.tserver.AgentSetReady('', self.dn.number, workMode, reasons, extensions, send=0)
                    self.dn.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self.dn,
                                                                          parties=self.dn.partyList,
                                                                          expectedAvailability=True)

                self.dn.tserver.AgentSetReady('', self.dn.number, workMode, reasons, extensions)
                self.workMode = workMode
                self.state = AgentState.Ready
                self.dn.eventAgentReady(None, addPrm={"ReferenceID": self.dn.tserver.refID})
            else:
                if self.defaultDN and self.defaultDN.tserver.SwitchPolicyFeature():
                    request = self.defaultDN.tserver.AgentSetReady('', self.defaultDN.number, workMode, reasons,
                                                                   extensions, send=0)
                    self.defaultDN.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self.defaultDN,
                                                                                 expectedAvailability=False)
                    return
                else:
                    ProgrammError("Agent is not logged into DN")

    def SetNotReady(self, queue=None, workMode=AgentWorkMode.AuxWork, reasons=None, extensions=None, ignoreState=0,
                    byDefault=0):
        if queue:
            return Agent.SetNotReady(self, queue, workMode, reasons, extensions,
                                     ignoreState, byDefault)
        else:
            if self.dn:
                bdf = ""
                if byDefault:
                    bdf = "by default"
                self.dn.printLog("\nSetNotReady (%s) * %s" % (self.id, bdf))
                if not ignoreState:
                    if self.state == AgentState.NotReady:
                        ProgrammWarning("Agent is in NotReady state")
                        return
                if not byDefault:
                    time.sleep(self.dn.tserver.requestTimout)
                    if self.dn.tserver.SwitchPolicyFeature():
                        request = self.dn.tserver.AgentSetNotReady('', self.dn.number, workMode, reasons, extensions,
                                                                   send=0)
                        self.dn.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self.dn,
                                                                              parties=self.dn.partyList,
                                                                              expectedAvailability=True)

                    refID = self.dn.tserver.AgentSetNotReady('', self.dn.number, workMode, reasons, extensions)
                else:
                    refID = 0
                if workMode == AgentWorkMode.AfterCallWork:
                    self.state = AgentState.AfterCallWork
                else:
                    self.state = AgentState.NotReady
                self.workMode = workMode
                addPrm = {"ReferenceID": refID}
                self.dn.eventAgentNotReady(None, addPrm=addPrm)
            else:
                if self.defaultDN and self.defaultDN.tserver.SwitchPolicyFeature():
                    request = self.defaultDN.tserver.AgentSetNotReady('', self.defaultDN.number, workMode, reasons,
                                                                      extensions, send=0)
                    self.defaultDN.tserver.SwitchPolicy.CheckFunctionAvailablity(request, dn=self.defaultDN,
                                                                                 expectedAvailability=False)
                    return
                else:
                    ProgrammError("Agent is not logged into DN")

    # overrrided methods are added to handle multiple login in one queue Vlad Gorelov fix 02/17/14 according to SIP-6083

    def loginEvents(self, workMode=AgentWorkMode.Unknown,
                    refID=0):  # this is hacked, careful processing needed, maybe override of dn.eventAgentLogin
        state = self.state
        self.dn.eventAgentLogin(None, addPrm={"ReferenceID": refID})
        if state != AgentState.Logout:
            self.state = state
        self.setStateAfterLogin(workMode)

    def setStateAfterLogin(self, workMode=AgentWorkMode.Unknown):
        """ overrided method handles situation when agent has already logged in """
        if self.state == AgentState.Login:
            self.state = AgentState.NotReady
        if self.state == AgentState.NotReady:
            self.dn.eventAgentNotReady(None, addPrm={"ReferenceID": 0})
        if self.state == AgentState.Ready:
            self.dn.eventAgentReady(None, addPrm={"ReferenceID": 0})


    def errorLoginEvents(self, queue, dn, refID):
        """ overrided method handles situation when agent has already logged in """
        addPrm = {"ReferenceID": refID}
        if dn.agentLogin and (dn.agentLogin != self):
            #Somebody else has logged on dn
            dn.eventError(None, addPrm=addPrm)
            return True

        elif self.dn and (dn != self.dn):
            #Agent log on several DNs
            dn.eventError(None, addPrm=addPrm)
            return True
        else:
            return False

            # end of fix

#==================================================================================
try:
    import model_switchpolicy
except:
    pass


class SIP_TServer(model_tserver.TServer):
    DN = SIP_DN
    Agent = SIP_Agent
    RouteDN = SIP_RouteDN
    Queue = SIP_Queue
    Position = SIP_DN
    Call = SIP_Call
    Party = SIP_Party
    Trunk = SIP_Trunk


    def __init__(self, servName="", appName="", appPassword="",
                 numberForInboundCall=None):
        model_tserver.TServer.__init__(self, servName, appName, appPassword, numberForInboundCall)
        if self.SwitchPolicyFeature():
            self.SwitchPolicy = model_switchpolicy.SPProvider("SIPSwitch")
        self.useNotMonitorNextCall = 0  # reset default
        if InFalse(GetOption("PJSip")):
            self.defaulTimeout = 32
        else:
            self.defaulTimeout = 64
        if GetOption("TLibDefaultTimeout"):
            self.defaulTimeout = int(GetOption("TLibDefaultTimeout"))
        self.sip_port = "5060"

        self.routeRequestOnlyOnRP = 0
        self.networkReached_on_ssx = 1
        self.default_network_call_id_matching = 'now'
        self.mwi_implicit_notify = 0

        self.IM_DN = None
        self.sipPhoneCleared = 0
        self.sipAddr = ""
        self.sipIPAddr = ""
        self.expectPrivateInfo = 0
        self.drop_nailedup_on_logout = "false"
        #this is the new option. when True all nailed-up connections are dropped after test Vlad Gorelov 06/24/14
        self.drop_nailedup_after_test = False
        # end of fix
        self.divert_on_ringing = "true"
        self.use_data_from = "current"  # fix by Vlad Gorelov CPTT-56
        # vgratsil 02/14/2014 - flag to set when Router connected to this TServer.
        # enables Wait all evemts on RP.
        self.isRouterConnected = False
        self.sip_enable_call_info = "false"
        self.trunk_optimization = []

    def set_optimization(self, ts):
        if not ts in self.trunk_optimization:
            self.trunk_optimization.append(ts)

    def remove_optimization(self, ts):
        if self.is_optimized(ts):
            self.trunk_optimization.remove(ts)

    def is_optimized(self, ts):
        if ts in self.trunk_optimization:
            return True
        return False

    def remove_call(self, call):
        if call in self.calls:
            self.calls.remove(call)

    def toggleRouterConnected(self, flagToSet=None):
        """Allows to control SIPS behavior by setting flag isRoouterConnected.
        Arguments:
            flagToSet - default value None. Should be either None or Boolean type (True, False).
            if set to None, then method inverses current isRouterConnected value.
            else - sets isRouterConnected to value in flagToSet.
        Returns:
            None"""
        if flagToSet is None:
            self.isRouterConnected = not (self.isRouterConnected)
            return
        self.isRouterConnected = flagToSet


    def setDefaultOptionValue(self, optionName):
        model_tserver.TServer.setDefaultOptionValue(self, optionName)
        if optionName == "enable_ess ":
            if InTrue(self.enable_ess):
                self.expectPrivateInfo = 1
            else:
                self.expectPrivateInfo = 0
        if optionName == "monitor_consult_calls":
            self.monitor_consult_calls = "none"
        # fix by Vlad Gorelov 08/06/13. Added handling of drop-nailedup-on-logout option on TServer
        if optionName == "drop_nailedup_on_logout":
            self.drop_nailedup_on_logout = "false"
        # end of fix
        if optionName == "divert_on_ringing":
            self.divert_on_ringing = "true"
        # fix by Vlad Gorelov CPTT-56
        if optionName == "use_data_from":
            self.use_data_from = "current"
        if optionName == "sip_enable_call_info":
            self.sip_enable_call_info = "false"


    def Open(self, toPrimOnly=0):
        model_tserver.TServer.Open(self, toPrimOnly)
        objects = self.cfgSwitch.cfgServer.GetObjectInfo(CfgObjectType.CFGDN,
                                                         {"dn_number": "gcti::im", "switch_dbid": self.cfgSwitch.DBID})
        if len(objects) == 1:
            self.IM_DN = self.IMMonitoringDN(self)
        else:
            self.IM_DN = None  #gcti::im MUST be configured in CME for CHAT testing (only)

        self.sipAddr = "%s:%s" % (self.Host, self.sip_port)
        self.sipIPAddr = "%s:%s" % (self.IPaddress, self.sip_port)
        if InTrue(self.enable_ess):
            if InTrue(GetOption("SessionEventsOn")):
                objects = self.cfgSwitch.cfgServer.GetObjectInfo(CfgObjectType.CFGDN,
                                                                 {"switch_dbid": self.cfgSwitch.DBID, "dn_type": 3})
                if not objects:
                    objects = self.cfgSwitch.cfgServer.GetObjectInfo(CfgObjectType.CFGDN,
                                                                     {"switch_dbid": self.cfgSwitch.DBID, "dn_type": 1})
                if objects:
                    num = self.cfgSwitch.cfgServer.GetObjectCharPropertyFromString(objects[0],
                                                                                   "number")  # get the first queue
                    self.RegisterAddress(num)
                    self.WaitEvent(eventName="Registered", timeout=2)
                    #serviceID = 3004
                    PrintLog("Requesting PrivateService %s" % SESSION_PRIVATE_SERVICE_ID)
                    self.PrivateService(SESSION_PRIVATE_SERVICE_ID, num)
                    ev = self.WaitEventOnDNStrict(num, "ACK", timeout=2)
                    self.expectPrivateInfo = 1

                else:
                    ProgrammWarning(
                        "TEMP: Private service does not work. Cannot get a number to send request. Will be changed in future")

            self.expectPrivateInfo = 1

        else:
            self.expectPrivateInfo = 0


    def getAddPrmForOldPartiesOnOptimize(self, newCall, prevConnID, initiator, cause):

        addPrm = {"PreviousConnID": newCall.ConnID}
        if cause in (Cause.CauseTransfer, Cause.Cause1stepTransfer):
            addPrm["CallState"] = CallState.Transferred
            if initiator:
                addPrm["ThirdPartyDN"] = initiator.number
                addPrm["ThirdPartyDNRole"] = PartyRole.TransferedBy

        if len(newCall.PartyList) == 2:
            otherDN = newCall.PartyList[1].DN  # party that joined last
            addPrm["OtherDN"] = otherDN
        return addPrm

    def getAddPrmForNewPartiesOnOptimize(self, newCall, prevConnID, initiator, cause):
        addPrm = {"PreviousConnID": prevConnID}
        if cause in (Cause.CauseTransfer, Cause.Cause1stepTransfer):
            addPrm["CallState"] = CallState.Transferred
            if initiator:
                addPrm["ThirdPartyDN"] = initiator.number
                addPrm["ThirdPartyDNRole"] = PartyRole.TransferedBy

        if len(newCall.PartyList) == 2:
            otherDN = newCall.PartyList[0].DN  # party that was first
            addPrm["OtherDN"] = otherDN
        return addPrm

    def ClearObjectList(self):
        """ Clear object list
        """
        self.getRemoteConnectionEvents()  # needed to print RemoteConnection events
        self.sipPhoneCleared = 0
        for obj in self.ObjectList:
            obj.Clear()
        self.sipPhoneCleared = 1
        self.reservedAgents = []
        self.IgnoredEventList = []
        self.calls = []

    def RestartSipEndpoint(self, number):
        dn = self.FindObjByNumber(number)
        if dn and hasattr(dn, "scsEndPoint") and dn.scsEndPoint:  # most likely it will need restart
            if InTrue(GetOption("DonotRestartEpiphoneOnOutOfService")):
                return 0
            PrintLog("DonotRestartEpiphoneOnOutOfService: " + GetOption("DonotRestartEpiphoneOnOutOfService"))
            PrintLogStat("Restarting endpoint")
            dn.SipPhone.phoneClient().Close()
            dn.scsEndPoint.StopNoEvents()
            while 1:
                ev = dn.scsEndPoint.scServer.WaitEvent(dn.scsEndPoint.cfgObj.DBID, dn.scsEndPoint.cfgObj.objType.val,
                                                       timeout=1)
                if not ev: break
                time.sleep(0.3)
            dn.scsEndPoint.GetInfo()
            dn.scsEndPoint.Start()

            if not dn.SipPhone.phoneClient():  # should reconnect here
                FatalError("No connection to EpiPhone")
            dn.SipPhone.SipRegisterAll()
            return 1
        return 0

    def actionOnUnexpectedEvent(self, number, event):
        if event.Event == EventName.DNOutOfService:
            if self.RestartSipEndpoint(number):
                self.SeriousError("DN Out Of Service. End point was restarted", forceReset=0)
                self.serErrBadOtherCnt = self.serErrBadOtherCnt + 1
        model_tserver.TServer.actionOnUnexpectedEvent(self, number, event)

    def actionOnUnexpectedEventInGetUnread(self, number, event):
        if event.Event == EventName.DNOutOfService:
            if self.RestartSipEndpoint(number):
                PrintLog("DN Out Of Service. End point was restarted")
        model_tserver.TServer.actionOnUnexpectedEventInGetUnread(self, number, event)

    def SetDefaultMask(self):
        model_tserver.TServer.MaskSet(self, EventName.NetworkReached, 0)
        self.WaitEvent(eventName="ACK", timeout=2)

    def addReset(self, number, addrType):
        self.SetDNDOff(number)
        self.WaitEvent(timeout=1)

    def GetUnprocessedEvents(self, unexpected=0, timeout=0):
        if unexpected:
            timeout = 0.1
        else:
            timeout = 0.05
        model_tserver.TServer.GetUnprocessedEvents(self, unexpected, timeout)

    def WaitPrivateInfoForSession(self, timeout=5):
        """Waits event PrivateInfo for SESSION_PRIVATE_INFO_ID. Returns ev or None, raises error if event not received
          parameters:
            timeout       - int
          return          - ev or None
        """
        PrintLog("\n  Waiting: PrivateInfo, PrivateMsgID = %s " % SESSION_PRIVATE_INFO_ID)
        ev = self.WaitEvent(["Empty", SESSION_PRIVATE_INFO_ID], eventName="PrivateInfo", timeout=timeout,
                            keyFields=["ThisDN", "PrivateEvent"])
        if not ev:
            if self.ActionOnNoEvent:
                self.ActionOnNoEvent("for PrivateMsgID %s " % SESSION_PRIVATE_INFO_ID, "PrivateInfo")
                return
        s = "    " + ev.__repr__()
        s = string.replace(s, "\n", "\n    ")
        PrintLog(s)
        return ev

    def GetSyncNotification(self, p1=(), p2=(), timeout=5):
        """Waits event PrivateInfo for SESSION_PRIVATE_INFO_ID. Verifies parameters. Raises error if event not received
          parameters:
            p1 - list names ("Add-1", )
            p2 (params) - dict
            timeout       - int
          return          - ev or None
        """
        ev = self.WaitPrivateInfoForSession(timeout=timeout)
        if ev:
            eventName = ev.Event
            seqNumber = ev.EventSequenceNumber
            len1 = len(p1)
            lenEx = len(ev.Extensions)
            if ev.Extensions:
                extsCopy = copy.copy(ev.Extensions)
                if len(p1) > 0 and len(p2) > 0 and (len(p1) == len(p2)):
                    while len1 > 0:
                        if not ev.Extensions.has_key(p1[len1 - 1]):
                            self.SeriousError("PrivateInfo seqNum %016x has no key %s, get %s" % (
                                seqNumber, p1[len1 - 1], ev.Extensions.keys()), forceReset=0)
                            self.serErrBadOtherCnt = self.serErrBadOtherCnt + 1
                        elif extsCopy[p1[len1 - 1]]:
                            print "received:", extsCopy[p1[len1 - 1]]
                            print "expected:", p2[len1 - 1]
                            self.VerifyValue(extsCopy[p1[len1 - 1]], p2[len1 - 1],
                                             description="Bad field Extensions in PrivateInfo, list % s, seqNum  %016x" % (
                                                 p1[len1 - 1], seqNumber), error=1)
                            del extsCopy[p1[len1 - 1]]
                        len1 = len1 - 1
                if len(extsCopy) > 0:
                    for k in extsCopy.keys():
                        if k.startswith("Del") or k.startswith("Add") or k.startswith("Snap") or k.startswith("Merge"):
                            self.SeriousError(
                                "PrivateInfo seqNum %016x unexpected key: ev.Extensions[%s]" % (seqNumber, k),
                                forceReset=0)
                            self.serErrBadOtherCnt = self.serErrBadOtherCnt + 1
            else:
                self.SeriousError("No Extensions in PrivateInfo for event seqNum  %016x" % (seqNumber), forceReset=0)

    def WaitPrivateInfoForSession_802(self, sessionID, timeout=5):
        """Waits event PrivateInfo for specified session ID. Returns ev or None, raises error if event not received
          parameters:
            sessionID     - string
            timeout       - int
          return          - ev or None
        """

        PrintLog("\n  Waiting: PrivateInfo for session %s " % sessionID)
        ev = self.WaitEvent(["Empty", {"SessionID": sessionID}], eventName="PrivateInfo", timeout=timeout,
                            keyFields=["ThisDN", "Extensions"])
        if not ev:
            if self.ActionOnNoEvent:
                self.ActionOnNoEvent("for session %s " % sessionID, "PrivateInfo")
                return
        s = "    " + ev.__repr__()
        s = string.replace(s, "\n", "\n    ")
        PrintLog(s)
        return ev

    def GetSyncNotification_802(self, sessionID, parameters={}, timeout=5):
        """Waits event PrivateInfo for specified session ID. Verifies parameters. Raises error if event not received
          parameters:
            sessionID     - string
            parameters    - dict
            timeout       - int
          return          - ev or None
        """
        ev = self.WaitPrivateInfoForSession(sessionID, timeout=timeout)
        if ev:
            eventName = ev.Event
            seqNumber = ev.EventSequenceNumber
            if ev.Extensions:
                self.VerifyValue(ev.Extensions, parameters,
                                 description="Bad field Extensions in PrivateInfo, SessionID = %s, SeqNumber %016x" % (
                                     sessionID, seqNumber), error=1)
            else:
                self.SeriousError("No Extensions in PrivateInfo for call %s" % (cl), forceReset=0)
                self.serErrBadOtherCnt = self.serErrBadOtherCnt + 1


    class IMMonitoringDN(ServiceDN):
        def __init__(self, tserver):
            ServiceDN.__init__(self, "gcti::im", type=AddressType.DN, tserver=tserver)


        def GetNotification(self, sender, content):
            self.printLog("\n  Waiting: UserEvent for: %s" % self.displayName())
            ev = self.tserver.WaitEvent(self.number, "UserEvent", timeout=3)
            if not ev:
                self.tserver.Warning("No UserEvent on 'gcti::im'")
                self.tserver.warBadOtherCnt = self.tserver.warBadOtherCnt + 1
            else:
                if ev.OtherDN <> sender:
                    self.tserver.countFaults(self.displayName(), ev, "OtherDN", (sender, ev.OtherDN))
                if not ev.Extensions or not ev.Extensions.has_key("im") or not (ev.Extensions["im"] == content):
                    self.tserver.countFaults(self.displayName(), ev, "Extensions", ({'im': content}, ev.Extensions))
