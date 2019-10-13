'use strict';

// Register `indexPage` component, along with its associated controller and template
angular.
  module('indexPage').
  component('indexPage', {
    templateUrl: 'ng/index-page/index-page.template.html',
    controller: ['$http', '$routeParams', '$scope', '$rootScope', function IndexPageController($http, $routeParams, $scope, $rootScope) {
        var self = this;
        self.scope = $scope;
        console.log($rootScope.project_data)
        
    }]
  });