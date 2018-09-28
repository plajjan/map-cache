# map-cache
map-cache is a package to provide a cached copy of simple mappings and to
automatically update those on regular schedule.

## Typical problem
When working with SNMP data there is commonly a need to lookup values in various
tables. This need largely stems from the limitation in the MIB language that you
cannot have nested lists/tables, so you have to heavily refer in between them.
In addition, MIBs do not offer using natural keys for tables, instead all
indices are integers, requiring another lookup to get the natural "key".

For example, one table refers to ifIndex 3 but all we really care about is that
this maps to the interface name (ifName) 'HundredGigE0/1/2/3'.

NCS makes it pretty easy to do these lookups, you just fetch
/devices/device{FOO}/live-status/IF-MIB/ifTable[ifName='HundredGigE0/1/2/3'] or
look at the index if you want to go the other direction. The problem with such a
lookup is that it doesn't translate well into SNMP, you can't actually ask for
the table row with ifName='HundredGigE0/1/2/3' so instead NCS fetches the entire
table and iterates over each entry trying to find a match. This is slow and if
you want it to be fast, there really is only way to do it and that is to build a
cache, which is precisely what this package is about.

## Solution - map-cache
A map helps you go from e.g. /IF-MIB/ifTable/ifName to /IF-MIB/ifTable/ifIndex,
i.e. do a lookup from a key to a value. It is perhaps easiest to understand by
looking at the YANG model;

module: map-cache
    +--rw map-cache
       +--rw enabled?          boolean
       +--rw worker-threads?   uint16
       +--rw map* [key_xpath value_xpath]
          +--rw key_xpath      string
          +--rw value_xpath    string
          +--rw device* [name]
             +--rw name               string
             +--rw update-interval?   uint32
             +--ro last-poll-stats
             |  +--ro start-timestamp?   yang:date-and-time
             |  +--ro end-timestamp?     yang:date-and-time
             |  +--ro duration?          yang:timeticks
             |  +--ro entries-polled?    uint32
             +--ro map* [k]
                +--ro k    string
                +--ro v?   string
                
/map-cache/map is the list with an entry for each mapping. The key consists of
two XPaths pointing out the key and values we want to map from and to
respectively. These serve both to identify this mapping but also act as
*configuration* of the map-cache application itself. This type of mapping is
unique per device, thus under each mapping definition, we have a list of
devices, and under each device we will find the actual mapping of table content.

To continue on the example with /IF-MIB/ifTable/ifName mapping to
/IF-MIB/ifTable/ifIndex, these would be the key_xpath and value_xpath of
/map-cache/map. We can find the ifIndex of HundredGigE0/1/2/3 on device FOO
using the following XPath into the map-cache:

/map-cache/map{/IF-MIB/ifTable/ifName,/IF-MIB/ifTable/ifIndex}/device{FOO}/map{HundredGigE0/1/2/3}/v

Note that the base of the map-cache tree is configuration, that is you write the
mappings you want and for which device you are interested in them. Background
worker thread will then populate this data and you can read out the final map as
operational data.


## Configuration options
You can enable or disable map-cache through the leaf /map-cache/enabled. Notice
that it can take some time, up to about a minute, for the background threads to
notice that they should not run. Currently running jobs to populate data will
not be canceled.

You can configure the number of background worker threads that will fetch data
through /map-cache/worker-threads. This defaults to 1.


## Internals
There is one thread that periodically adds job to a queue and then there are one
or more worker threads that get jobs from the queue and perform the actual
polling and population of the map-cache.
